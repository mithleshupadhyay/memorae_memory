from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from memorae_memory.query import QueryProfile
from memorae_memory.retrieval.bm25 import BM25Index, tokenize
from memorae_memory.schemas import CandidateEvent, EventRecord, EventSignal, QueryIntent
from memorae_memory.time_utils import next_day_start_ist, start_of_day_ist

CONSEQUENCE_PATTERNS = (
    "blocks",
    "blocked",
    "depends on",
    "if you cannot",
    "late fee",
    "or i will",
    "portal locks",
    "release the",
    "required",
)
DEPENDENCY_PATTERNS = (
    "blocked",
    "depends on",
    "dependency",
    "not on",
    "waiting on",
)
REPEATED_ASK_PATTERNS = (
    "again",
    "friendly nudge",
    "nudge",
    "slips again",
    "still need",
)


@dataclass(frozen=True)
class RankingConfig:
    max_reason_count: int = 8
    salience_weight: float = 0.25
    topic_match_base: float = 2.4
    outside_topic_penalty: float = 1.8
    actionable_boost: float = 0.35
    commitment_boost: float = 0.45
    update_boost: float = 0.75
    preference_boost: float = 0.35
    noise_penalty: float = 3.0
    future_near_deadline_penalty: float = 0.85
    future_non_calendar_penalty: float = 1.1
    topic_summary_bm25_weight: float = 0.55
    today_bm25_weight: float = 0.75
    default_bm25_weight: float = 1.0
    topic_min_score: float = 0.35
    intent_min_score: float = 0.75
    default_min_score: float = 0.25
    today_urgency_weight: float = 0.9
    today_due_or_scheduled_boost: float = 2.5
    today_calendar_anchor_boost: float = 0.9
    today_overdue_boost: float = 1.6
    today_scheduled_outside_penalty: float = 1.8
    today_near_deadline_boost: float = 1.2
    today_consequence_boost: float = 1.5
    today_no_signal_penalty: float = 2.5
    risk_overdue_boost: float = 2.6
    risk_due_48h_boost: float = 2.2
    risk_due_week_boost: float = 0.9
    risk_commitment_boost: float = 1.0
    risk_reminder_boost: float = 0.8
    risk_consequence_boost: float = 1.8
    risk_no_commitment_penalty: float = 1.2
    procrastination_repeated_ask_boost: float = 2.2
    procrastination_past_due_boost: float = 2.1
    procrastination_stale_action_boost: float = 1.2
    procrastination_repeated_topic_boost: float = 0.9
    procrastination_actionable_boost: float = 0.8
    procrastination_no_signal_penalty: float = 1.0
    summary_update_boost: float = 1.8
    summary_dated_boost: float = 0.8
    summary_action_boost: float = 0.65
    summary_preference_boost: float = 0.5
    summary_dependency_boost: float = 1.0
    summary_topic_context_boost: float = 0.5
    summary_stale_deadline_penalty_weight: float = 0.7
    summary_recency_weight: float = 0.75
    generic_urgency_weight: float = 0.3
    generic_update_boost: float = 0.3
    generic_topic_boost: float = 0.25
    old_deadline_penalty: float = 2.2
    older_deadline_penalty: float = 1.0

    def __post_init__(self) -> None:
        if self.max_reason_count < 1:
            raise ValueError("max_reason_count must be at least 1.")


class CandidateRanker:
    def __init__(
        self,
        events: list[EventRecord],
        signals: dict[int, EventSignal],
        now: datetime,
        config: RankingConfig | None = None,
    ) -> None:
        self.events = list(events)
        self.signals = dict(signals)
        self.now = now
        self.config = config or RankingConfig()
        self._validate_inputs()
        self.topic_event_counts = self._build_topic_event_counts()
        self.index = BM25Index(self.events, extra_terms=self._build_index_extra_terms())

    def retrieve(
        self,
        profile: QueryProfile,
        max_candidates: int = 80,
    ) -> list[CandidateEvent]:
        if max_candidates < 1:
            raise ValueError("max_candidates must be at least 1.")

        lexical_query = self._build_lexical_query(profile)
        bm25_weight = self._bm25_weight(profile)
        minimum_score = self._minimum_score(profile)
        candidates: list[CandidateEvent] = []

        for event in self.events:
            signal = self.signals[event.event_id]
            if (
                profile.intent == QueryIntent.TOPIC_SUMMARY
                and profile.selected_topics
                and not (signal.topics & profile.selected_topics)
            ):
                continue

            bm25_score = self.index.score(lexical_query, event.event_id)
            signal_score, reasons, downrank_reasons, score_breakdown = self._score_event(
                event=event,
                signal=signal,
                profile=profile,
            )
            score_breakdown["bm25"] = bm25_score * bm25_weight
            final_score = score_breakdown["bm25"] + signal_score
            if bm25_score > 0:
                reasons.append("lexical match to query terms")
            if final_score <= minimum_score:
                continue

            candidates.append(
                CandidateEvent(
                    event=event,
                    signal=signal,
                    score=final_score,
                    reasons=reasons[: self.config.max_reason_count],
                    downrank_reasons=downrank_reasons,
                    score_breakdown={
                        key: round(value, 3) for key, value in score_breakdown.items() if value != 0
                    },
                )
            )

        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.score,
                candidate.signal.is_update,
                candidate.event.timestamp,
                -candidate.event.event_id,
            ),
            reverse=True,
        )[:max_candidates]

    def _validate_inputs(self) -> None:
        event_ids = [event.event_id for event in self.events]
        event_id_set = set(event_ids)
        duplicate_ids = sorted(
            event_id for event_id, count in Counter(event_ids).items() if count > 1
        )
        missing_signal_ids = sorted(event_id_set - set(self.signals))
        orphan_signal_ids = sorted(set(self.signals) - event_id_set)

        validation_errors: list[str] = []
        if duplicate_ids:
            validation_errors.append(f"duplicate event ids: {duplicate_ids[:5]}")
        if missing_signal_ids:
            validation_errors.append(f"missing signals for event ids: {missing_signal_ids[:5]}")
        if orphan_signal_ids:
            validation_errors.append(f"signals without matching events: {orphan_signal_ids[:5]}")
        if validation_errors:
            raise ValueError("Invalid ranking inputs: " + "; ".join(validation_errors))

    def _build_topic_event_counts(self) -> Counter[str]:
        topic_event_counts: Counter[str] = Counter()
        for signal in self.signals.values():
            topic_event_counts.update(signal.topics)
        return topic_event_counts

    def _build_index_extra_terms(self) -> dict[int, list[str]]:
        extra_terms: dict[int, list[str]] = {}
        for event in self.events:
            signal = self.signals[event.event_id]
            terms = list(signal.topics)
            terms.extend(topic.replace("_", " ") for topic in signal.topics)
            terms.extend(signal.reason_codes)
            if signal.due_at:
                terms.append("deadline")
            if signal.scheduled_at:
                terms.append("calendar")
            extra_terms[event.event_id] = terms
        return extra_terms

    def _build_lexical_query(self, profile: QueryProfile) -> str:
        lexical_terms = profile.query_terms or set(tokenize(profile.query))
        if profile.selected_topics:
            lexical_terms = lexical_terms | {
                topic.replace("_", " ") for topic in profile.selected_topics
            }
        return " ".join(sorted(lexical_terms))

    def _bm25_weight(self, profile: QueryProfile) -> float:
        if profile.intent == QueryIntent.TOPIC_SUMMARY and profile.selected_topics:
            return self.config.topic_summary_bm25_weight
        if profile.intent == QueryIntent.TODAY_FOCUS:
            return self.config.today_bm25_weight
        return self.config.default_bm25_weight

    def _minimum_score(self, profile: QueryProfile) -> float:
        if profile.selected_topics:
            return self.config.topic_min_score
        if profile.intent in {
            QueryIntent.TODAY_FOCUS,
            QueryIntent.RISK_MISSING,
            QueryIntent.PROCRASTINATION,
        }:
            return self.config.intent_min_score
        return self.config.default_min_score

    def _score_event(
        self,
        event: EventRecord,
        signal: EventSignal,
        profile: QueryProfile,
    ) -> tuple[float, list[str], list[str], dict[str, float]]:
        score_breakdown = {"salience": signal.salience_score * self.config.salience_weight}
        score = score_breakdown["salience"]
        reasons: list[str] = []
        downrank_reasons: list[str] = []
        content_lower = event.content.lower()

        topic_match = signal.topics & profile.selected_topics
        if topic_match:
            topic_score = max(profile.topic_scores.get(topic, 0.0) for topic in topic_match)
            boost = self.config.topic_match_base + topic_score
            score += boost
            score_breakdown["topic_match"] = boost
            reasons.append(f"matches inferred topic cluster: {', '.join(sorted(topic_match))}")
        elif profile.selected_topics:
            score -= self.config.outside_topic_penalty
            score_breakdown["outside_topic_penalty"] = -self.config.outside_topic_penalty
            downrank_reasons.append("outside inferred query topic")
        elif signal.topics:
            reasons.append(f"derived topic signal: {', '.join(sorted(signal.topics))}")

        if signal.is_actionable:
            score += self.config.actionable_boost
            score_breakdown["actionable"] = self.config.actionable_boost
            reasons.append("actionable language")
        if signal.is_commitment:
            score += self.config.commitment_boost
            score_breakdown["commitment"] = self.config.commitment_boost
            reasons.append("commitment/deadline language")
        if signal.is_update:
            score += self.config.update_boost
            score_breakdown["update"] = self.config.update_boost
            reasons.append("update or correction")
        if signal.is_preference and (profile.intent == QueryIntent.TOPIC_SUMMARY or topic_match):
            score += self.config.preference_boost
            score_breakdown["preference"] = self.config.preference_boost
            reasons.append("preference/style constraint")
        if signal.is_noise:
            score -= self.config.noise_penalty
            score_breakdown["noise_penalty"] = -self.config.noise_penalty
            downrank_reasons.append("low-signal/noisy event")

        tomorrow_start = next_day_start_ist(self.now)
        if signal.is_future_observation and event.source not in {"calendar", "reminder"}:
            if signal.due_at and signal.due_at < tomorrow_start:
                score -= self.config.future_near_deadline_penalty
                score_breakdown[
                    "future_observation_penalty"
                ] = -self.config.future_near_deadline_penalty
                downrank_reasons.append("future message, but near-term deadline")
            else:
                score -= self.config.future_non_calendar_penalty
                score_breakdown[
                    "future_observation_penalty"
                ] = -self.config.future_non_calendar_penalty
                downrank_reasons.append("non-calendar event after scenario time")

        if profile.intent == QueryIntent.TODAY_FOCUS:
            intent_score = self._score_today_signal(
                event=event,
                signal=signal,
                content_lower=content_lower,
                reasons=reasons,
                downrank_reasons=downrank_reasons,
            )
            score_breakdown["today_focus_signal"] = intent_score
        elif profile.intent == QueryIntent.RISK_MISSING:
            intent_score = self._score_risk_signal(
                event=event,
                signal=signal,
                content_lower=content_lower,
                reasons=reasons,
                downrank_reasons=downrank_reasons,
            )
            score_breakdown["risk_signal"] = intent_score
        elif profile.intent == QueryIntent.PROCRASTINATION:
            intent_score = self._score_procrastination_signal(
                event=event,
                signal=signal,
                content_lower=content_lower,
                reasons=reasons,
                downrank_reasons=downrank_reasons,
            )
            score_breakdown["procrastination_signal"] = intent_score
        elif profile.intent == QueryIntent.TOPIC_SUMMARY:
            intent_score = self._score_summary_signal(
                event=event,
                signal=signal,
                content_lower=content_lower,
                profile=profile,
                reasons=reasons,
                downrank_reasons=downrank_reasons,
            )
            score_breakdown["summary_signal"] = intent_score
        else:
            intent_score = signal.urgency_score * self.config.generic_urgency_weight
            if signal.is_update:
                intent_score += self.config.generic_update_boost
            if signal.topics:
                intent_score += self.config.generic_topic_boost
            score_breakdown["generic_signal"] = intent_score

        score += intent_score
        return score, reasons, downrank_reasons, score_breakdown

    def _score_today_signal(
        self,
        event: EventRecord,
        signal: EventSignal,
        content_lower: str,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        score = signal.urgency_score * self.config.today_urgency_weight
        today_start = start_of_day_ist(self.now)
        tomorrow_start = next_day_start_ist(self.now)
        due_today = bool(signal.due_at and today_start <= signal.due_at < tomorrow_start)
        scheduled_today = bool(
            signal.scheduled_at and today_start <= signal.scheduled_at < tomorrow_start
        )

        if due_today or scheduled_today:
            score += self.config.today_due_or_scheduled_boost
            reasons.append("due or scheduled today")
        if event.source == "calendar" and scheduled_today:
            score += self.config.today_calendar_anchor_boost
            reasons.append("current-day calendar anchor")
        if signal.due_at and signal.due_at < self.now:
            score += self.config.today_overdue_boost
            reasons.append("already overdue")
            score -= self._stale_deadline_penalty(signal.due_at, downrank_reasons)
        if signal.scheduled_at and not scheduled_today:
            score -= self.config.today_scheduled_outside_penalty
            downrank_reasons.append("scheduled outside requested day")
        if signal.due_at and self.now <= signal.due_at <= self.now + timedelta(hours=48):
            score += self.config.today_near_deadline_boost
            reasons.append("near-term deadline")
        if any(pattern in content_lower for pattern in CONSEQUENCE_PATTERNS):
            score += self.config.today_consequence_boost
            reasons.append("explicit consequence if missed")
        if not (
            signal.topics or signal.is_actionable or signal.is_commitment or signal.scheduled_at
        ):
            score -= self.config.today_no_signal_penalty
            downrank_reasons.append("no today/action signal")
        return score

    def _score_risk_signal(
        self,
        event: EventRecord,
        signal: EventSignal,
        content_lower: str,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        score = signal.urgency_score
        if signal.due_at and signal.due_at < self.now:
            score += self.config.risk_overdue_boost
            reasons.append("missed or overdue deadline")
            score -= self._stale_deadline_penalty(signal.due_at, downrank_reasons)
        elif signal.due_at and signal.due_at <= self.now + timedelta(hours=48):
            score += self.config.risk_due_48h_boost
            reasons.append("deadline inside 48 hours")
        elif signal.due_at and signal.due_at <= self.now + timedelta(days=7):
            score += self.config.risk_due_week_boost
            reasons.append("deadline inside a week")

        if signal.is_commitment:
            score += self.config.risk_commitment_boost
        if event.source == "reminder":
            score += self.config.risk_reminder_boost
            reasons.append("dated reminder")
        if any(pattern in content_lower for pattern in CONSEQUENCE_PATTERNS):
            score += self.config.risk_consequence_boost
            reasons.append("explicit consequence if missed")
        if not (signal.is_commitment or signal.due_at or signal.scheduled_at):
            score -= self.config.risk_no_commitment_penalty
            downrank_reasons.append("no explicit commitment or deadline")
        return score

    def _score_procrastination_signal(
        self,
        event: EventRecord,
        signal: EventSignal,
        content_lower: str,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        score = 0.0
        event_age_days = max((self.now - event.timestamp).days, 0)
        has_repeated_ask = any(pattern in content_lower for pattern in REPEATED_ASK_PATTERNS)

        if has_repeated_ask:
            score += self.config.procrastination_repeated_ask_boost
            reasons.append("repeated nudge or slipping language")
        if signal.due_at and signal.due_at < self.now:
            score += self.config.procrastination_past_due_boost
            reasons.append("deadline has passed")
        if event_age_days >= 4 and (signal.is_actionable or signal.is_commitment):
            score += self.config.procrastination_stale_action_boost
            reasons.append("action has been open for several days")
        if signal.topics and any(self.topic_event_counts[topic] >= 3 for topic in signal.topics):
            score += self.config.procrastination_repeated_topic_boost
            reasons.append("topic has repeated actionable events")
        if signal.is_actionable or signal.is_commitment:
            score += self.config.procrastination_actionable_boost
        if not (signal.is_actionable or signal.is_commitment or has_repeated_ask):
            score -= self.config.procrastination_no_signal_penalty
            downrank_reasons.append("no repeated/open-action signal")
        return score

    def _score_summary_signal(
        self,
        event: EventRecord,
        signal: EventSignal,
        content_lower: str,
        profile: QueryProfile,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        score = 0.0
        if signal.is_update:
            score += self.config.summary_update_boost
        if signal.due_at or signal.scheduled_at:
            score += self.config.summary_dated_boost
        if signal.is_actionable or signal.is_commitment:
            score += self.config.summary_action_boost
        if signal.is_preference:
            score += self.config.summary_preference_boost
        if any(pattern in content_lower for pattern in DEPENDENCY_PATTERNS):
            score += self.config.summary_dependency_boost
            reasons.append("dependency or blocker signal")
        if signal.topics and not profile.selected_topics:
            score += self.config.summary_topic_context_boost
        if signal.due_at and signal.due_at < self.now and not signal.is_update:
            score -= (
                self._stale_deadline_penalty(signal.due_at, downrank_reasons)
                * self.config.summary_stale_deadline_penalty_weight
            )

        age_days = max((self.now - event.timestamp).total_seconds() / 86400, 0.0)
        if age_days <= 1:
            score += 1.0 * self.config.summary_recency_weight
        elif age_days <= 3:
            score += 0.7 * self.config.summary_recency_weight
        elif age_days <= 7:
            score += 0.45 * self.config.summary_recency_weight
        else:
            score += 0.2 * self.config.summary_recency_weight
        return score

    def _stale_deadline_penalty(
        self,
        due_at: datetime,
        downrank_reasons: list[str],
    ) -> float:
        age = self.now - due_at
        if age > timedelta(days=5):
            downrank_reasons.append("old overdue signal")
            return self.config.old_deadline_penalty
        if age > timedelta(days=2):
            downrank_reasons.append("older overdue signal")
            return self.config.older_deadline_penalty
        return 0.0

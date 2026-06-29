from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import sqrt

from memorae_memory.events import load_events
from memorae_memory.retrieval.bm25 import BM25Index, tokenize
from memorae_memory.retrieval.context_builder import build_context
from memorae_memory.schemas import (
    CandidateEvent,
    ContextBuildResponse,
    ContextEvent,
    EventRecord,
    EventSignal,
    JsonObject,
    QueryIntent,
    QueryResponse,
)
from memorae_memory.signals import extract_signals
from memorae_memory.time_utils import (
    SCENARIO_NOW,
    format_ist,
    format_utc,
    next_day_start_ist,
    start_of_day_ist,
)

DEFAULT_QUERIES = [
    "What should I focus on today?",
    "What commitments am I at risk of missing?",
    "What have I been procrastinating on?",
    "Summarize everything related to the UIE proposal.",
]

STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "at",
    "be",
    "been",
    "by",
    "can",
    "do",
    "everything",
    "for",
    "from",
    "have",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "related",
    "should",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "which",
    "with",
}
SUMMARY_TERMS = {"summarize", "summary", "recap", "status", "related", "everything"}
TODAY_TERMS = {"today", "focus", "priority", "prioritize", "now"}
RISK_TERMS = {"risk", "missing", "miss", "commitment", "commitments", "deadline", "due"}
PROCRASTINATION_TERMS = {
    "procrastinating",
    "procrastinate",
    "delayed",
    "stalling",
    "putting",
    "slipping",
}
TOPIC_QUERY_STOPWORDS = (
    STOPWORDS | SUMMARY_TERMS | TODAY_TERMS | RISK_TERMS | PROCRASTINATION_TERMS
)
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
COMPLETION_PATTERNS = (
    "closed",
    "complete",
    "completed",
    "done",
    "sent",
    "submitted",
)
TOPIC_ACRONYMS = {
    "q2": "Q2",
    "sow": "SOW",
    "uie": "UIE",
}


@dataclass(frozen=True)
class QueryProfile:
    query: str
    intent: QueryIntent
    query_terms: set[str]
    selected_topics: set[str] = field(default_factory=set)
    topic_scores: dict[str, float] = field(default_factory=dict)

    @property
    def wants_today(self) -> bool:
        return self.intent == QueryIntent.TODAY_FOCUS

    @property
    def wants_risk(self) -> bool:
        return self.intent == QueryIntent.RISK_MISSING

    @property
    def wants_procrastination(self) -> bool:
        return self.intent == QueryIntent.PROCRASTINATION

    @property
    def wants_summary(self) -> bool:
        return self.intent == QueryIntent.TOPIC_SUMMARY


class MemoryEngine:
    def __init__(
        self,
        events: list[EventRecord],
        now: datetime = SCENARIO_NOW,
    ) -> None:
        self.events = events
        self.now = now
        self.signals = extract_signals(events, now=now)
        self.topic_terms = self._build_topic_terms()
        self.topic_event_counts = self._build_topic_event_counts()
        self.index = BM25Index(events, extra_terms=self._extra_terms())

    @classmethod
    def from_dataset(
        cls,
        dataset_path: str,
        now: datetime = SCENARIO_NOW,
    ) -> MemoryEngine:
        return cls(load_events(dataset_path), now=now)

    def answer(self, query: str) -> QueryResponse:
        profile = self.analyze_query(query)
        candidates = self.retrieve(query=query, profile=profile)
        context = build_context(
            candidates,
            max_events=_max_events_for_profile(profile),
            max_tokens=_max_tokens_for_profile(profile),
            max_events_per_topic=_topic_cap_for_profile(profile),
        )
        answer = self._generate_answer(profile, context)
        reasoning = self._build_reasoning(profile, context, candidates)

        return QueryResponse(
            query=query,
            intent=profile.intent,
            answer=answer,
            selected_context=context.context,
            reasoning=reasoning,
            diagnostics={
                "scenario_now_utc": format_utc(self.now),
                "scenario_now_ist": format_ist(self.now),
                "candidate_count": len(candidates),
                "selected_event_count": len(context.context),
                "selected_token_estimate": context.token_count,
                "ignored_summary": context.ignored_summary,
                "retrieval_strategy": (
                    "BM25 lexical recall plus runtime signal scoring. Topic constraints are "
                    "inferred from query/event term overlap, not from a list of expected questions."
                ),
                "future_event_policy": (
                    "Calendar/reminder events after scenario time are treated as known future "
                    "schedule. Non-calendar future timestamps are downweighted unless they carry "
                    "a near-term deadline or explicit update."
                ),
            },
        )

    def analyze_query(self, query: str) -> QueryProfile:
        query_terms = {term for term in tokenize(query) if term not in STOPWORDS}
        topic_terms = {term for term in query_terms if term not in TOPIC_QUERY_STOPWORDS}
        topic_scores = self._score_topics_for_query(topic_terms)
        selected_topics = _select_query_topics(topic_scores)
        intent = classify_intent(query)
        return QueryProfile(
            query=query,
            intent=intent,
            query_terms=query_terms,
            selected_topics=selected_topics,
            topic_scores=topic_scores,
        )

    def retrieve(
        self,
        query: str,
        profile: QueryProfile | None = None,
        max_candidates: int = 80,
    ) -> list[CandidateEvent]:
        profile = profile or self.analyze_query(query)
        lexical_query = _lexical_query(profile)
        bm25_weight = _bm25_weight(profile)
        candidates: list[CandidateEvent] = []

        for event in self.events:
            signal = self.signals[event.event_id]
            if _outside_selected_summary_topic(profile, signal):
                continue
            bm25_score = self.index.score(lexical_query, event.event_id)
            signal_score, reasons, downrank_reasons = self._score_signal(
                event,
                signal,
                profile,
            )
            score = (bm25_score * bm25_weight) + signal_score

            if bm25_score > 0:
                reasons.append("lexical match to query terms")
            if score <= _minimum_score(profile):
                continue

            candidates.append(
                CandidateEvent(
                    event=event,
                    signal=signal,
                    score=score,
                    reasons=reasons[:8],
                    downrank_reasons=downrank_reasons,
                )
            )

        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.score,
                candidate.signal.is_update,
                candidate.event.timestamp,
            ),
            reverse=True,
        )[:max_candidates]

    def _extra_terms(self) -> dict[int, list[str]]:
        extra_terms: dict[int, list[str]] = {}
        for event in self.events:
            signal = self.signals[event.event_id]
            terms = list(signal.topics)
            terms.extend(_topic_label(topic) for topic in signal.topics)
            terms.extend(signal.reason_codes)
            if signal.due_at:
                terms.append("deadline")
            if signal.scheduled_at:
                terms.append("calendar")
            extra_terms[event.event_id] = terms
        return extra_terms

    def _build_topic_terms(self) -> dict[str, Counter[str]]:
        topic_terms: dict[str, Counter[str]] = defaultdict(Counter)
        for event in self.events:
            signal = self.signals[event.event_id]
            useful_terms = [
                term
                for term in tokenize(event.content)
                if term not in STOPWORDS and len(term) > 2
            ]
            for topic in signal.topics:
                topic_terms[topic].update(useful_terms)
                topic_terms[topic].update(tokenize(_topic_label(topic)))
        return dict(topic_terms)

    def _build_topic_event_counts(self) -> Counter[str]:
        topic_event_counts: Counter[str] = Counter()
        for signal in self.signals.values():
            topic_event_counts.update(signal.topics)
        return topic_event_counts

    def _score_topics_for_query(self, query_terms: set[str]) -> dict[str, float]:
        if not query_terms:
            return {}

        topic_scores: dict[str, float] = {}
        normalizer = sqrt(len(query_terms))
        for topic, terms in self.topic_terms.items():
            overlap_score = 0.0
            for term in query_terms:
                if term not in terms:
                    continue
                overlap_score += 1.0 + min(terms[term], 4) * 0.25

            label_terms = set(tokenize(_topic_label(topic)))
            label_overlap = query_terms & label_terms
            if label_overlap:
                overlap_score += 1.25 * len(label_overlap)

            if overlap_score > 0:
                topic_scores[topic] = round(overlap_score / normalizer, 3)

        return dict(sorted(topic_scores.items(), key=lambda item: item[1], reverse=True))

    def _score_signal(
        self,
        event: EventRecord,
        signal: EventSignal,
        profile: QueryProfile,
    ) -> tuple[float, list[str], list[str]]:
        score = signal.salience_score * 0.25
        reasons: list[str] = []
        downrank_reasons: list[str] = []

        topic_match = signal.topics & profile.selected_topics
        if topic_match:
            boost = 2.4 + max(profile.topic_scores.get(topic, 0.0) for topic in topic_match)
            score += boost
            reasons.append(f"matches inferred topic cluster: {', '.join(sorted(topic_match))}")
        elif profile.selected_topics:
            score -= 1.8
            downrank_reasons.append("outside inferred query topic")
        elif signal.topics:
            reasons.append(f"derived topic signal: {', '.join(sorted(signal.topics))}")

        if signal.is_actionable:
            score += 0.35
            reasons.append("actionable language")
        if signal.is_commitment:
            score += 0.45
            reasons.append("commitment/deadline language")
        if signal.is_update:
            score += 0.75
            reasons.append("update or correction")
        if signal.is_preference and (profile.wants_summary or topic_match):
            score += 0.35
            reasons.append("preference/style constraint")
        if signal.is_noise:
            score -= 3.0
            downrank_reasons.append("low-signal/noisy event")

        if signal.is_future_observation and event.source not in {"calendar", "reminder"}:
            if signal.due_at and signal.due_at < next_day_start_ist(self.now):
                score -= 0.85
                downrank_reasons.append("future message, but near-term deadline")
            else:
                score -= 1.1
                downrank_reasons.append("non-calendar event after scenario time")

        score += self._score_temporal_and_task_fit(event, signal, profile, reasons, downrank_reasons)

        return score, reasons, downrank_reasons

    def _score_temporal_and_task_fit(
        self,
        event: EventRecord,
        signal: EventSignal,
        profile: QueryProfile,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        score = 0.0
        content_lower = event.content.lower()

        if profile.wants_today:
            score += signal.urgency_score * 0.9
            if _is_today(signal.due_at, self.now) or _is_today(signal.scheduled_at, self.now):
                score += 2.5
                reasons.append("due or scheduled today")
            if event.source == "calendar" and _is_today(signal.scheduled_at, self.now):
                score += 0.9
                reasons.append("current-day calendar anchor")
            if signal.due_at and signal.due_at < self.now:
                score += 1.6
                reasons.append("already overdue")
                score -= _stale_deadline_penalty(signal.due_at, self.now, downrank_reasons)
            if signal.scheduled_at and not _is_today(signal.scheduled_at, self.now):
                score -= 1.8
                downrank_reasons.append("scheduled outside requested day")
            if signal.due_at and self.now <= signal.due_at <= self.now + timedelta(hours=48):
                score += 1.2
                reasons.append("near-term deadline")
            if _has_explicit_consequence(content_lower):
                score += 1.5
                reasons.append("explicit consequence if missed")
            if not (signal.topics or signal.is_actionable or signal.is_commitment or signal.scheduled_at):
                score -= 2.5
                downrank_reasons.append("no today/action signal")

        elif profile.wants_risk:
            score += signal.urgency_score
            if signal.due_at and signal.due_at < self.now:
                score += 2.6
                reasons.append("missed or overdue deadline")
                score -= _stale_deadline_penalty(signal.due_at, self.now, downrank_reasons)
            elif signal.due_at and signal.due_at <= self.now + timedelta(hours=48):
                score += 2.2
                reasons.append("deadline inside 48 hours")
            elif signal.due_at and signal.due_at <= self.now + timedelta(days=7):
                score += 0.9
                reasons.append("deadline inside a week")
            if signal.is_commitment:
                score += 1.0
            if event.source == "reminder":
                score += 0.8
                reasons.append("dated reminder")
            if _has_explicit_consequence(content_lower):
                score += 1.8
                reasons.append("explicit consequence if missed")
            if not (signal.is_commitment or signal.due_at or signal.scheduled_at):
                score -= 1.2
                downrank_reasons.append("no explicit commitment or deadline")

        elif profile.wants_procrastination:
            event_age_days = max((self.now - event.timestamp).days, 0)
            if _has_repeated_ask_language(content_lower):
                score += 2.2
                reasons.append("repeated nudge or slipping language")
            if signal.due_at and signal.due_at < self.now:
                score += 2.1
                reasons.append("deadline has passed")
            if event_age_days >= 4 and (signal.is_actionable or signal.is_commitment):
                score += 1.2
                reasons.append("action has been open for several days")
            if signal.topics and self._topic_has_repeated_actions(signal):
                score += 0.9
                reasons.append("topic has repeated actionable events")
            if signal.is_actionable or signal.is_commitment:
                score += 0.8
            if not (signal.is_actionable or signal.is_commitment or _has_repeated_ask_language(content_lower)):
                score -= 1.0
                downrank_reasons.append("no repeated/open-action signal")

        elif profile.wants_summary:
            if signal.is_update:
                score += 1.8
            if signal.due_at or signal.scheduled_at:
                score += 0.8
            if signal.is_actionable or signal.is_commitment:
                score += 0.65
            if signal.is_preference:
                score += 0.5
            if _has_dependency_language(content_lower):
                score += 1.0
                reasons.append("dependency or blocker signal")
            if signal.topics and not profile.selected_topics:
                score += 0.5
            if signal.due_at and signal.due_at < self.now and not signal.is_update:
                score -= _stale_deadline_penalty(signal.due_at, self.now, downrank_reasons) * 0.7
            score += _recency_score(event.timestamp, self.now) * 0.75

        else:
            score += signal.urgency_score * 0.3
            if signal.is_update:
                score += 0.3
            if signal.topics:
                score += 0.25

        return score

    def _topic_has_repeated_actions(self, signal: EventSignal) -> bool:
        return any(self.topic_event_counts[topic] >= 3 for topic in signal.topics)

    def _generate_answer(self, profile: QueryProfile, context: ContextBuildResponse) -> str:
        if not context.context:
            return "I could not find enough grounded context in the event stream to answer that."

        if profile.wants_summary:
            return self._answer_topic_summary(profile, context)
        if profile.wants_today:
            return self._answer_prioritized(
                heading=f"At {format_ist(self.now)}, focus on these items first:",
                context=context,
                max_items=6,
            )
        if profile.wants_risk:
            return self._answer_prioritized(
                heading="Highest-risk commitments in the selected memory context:",
                context=context,
                max_items=7,
            )
        if profile.wants_procrastination:
            return self._answer_prioritized(
                heading="Strongest procrastination signals:",
                context=context,
                max_items=7,
            )
        return self._answer_generic(context)

    def _answer_topic_summary(
        self,
        profile: QueryProfile,
        context: ContextBuildResponse,
    ) -> str:
        topic_title = _topic_title(profile.selected_topics)
        stale_deadline_items = {
            item.event.event_id
            for item in context.context
            if _has_stale_deadline(item, self.now) and not item.signal.is_update
        }
        sections = [
            (
                "Latest updates and corrections",
                [
                    item
                    for item in context.context
                    if item.signal.is_update or _has_update_language(item.event.content.lower())
                ],
            ),
            (
                "Deadlines and calendar anchors",
                [
                    item
                    for item in context.context
                    if (item.signal.due_at or item.signal.scheduled_at)
                    and item.event.event_id not in stale_deadline_items
                ],
            ),
            (
                "Open asks and dependencies",
                [
                    item
                    for item in context.context
                    if (item.signal.is_actionable or item.signal.is_commitment)
                    and item.event.event_id not in stale_deadline_items
                ],
            ),
            (
                "Preferences and useful background",
                [item for item in context.context if item.signal.is_preference],
            ),
        ]

        lines = [f"{topic_title} summary, grounded in {len(context.context)} selected events."]
        used_event_ids: set[int] = set()
        for section_title, items in sections:
            unique_items = [
                item for item in _unique_context_events(items) if item.event.event_id not in used_event_ids
            ][:5]
            if not unique_items:
                continue
            lines.append(f"\n{section_title}:")
            for item in unique_items:
                lines.append(f"- {_format_context_event(item)}")
                used_event_ids.add(item.event.event_id)

        remaining = [
            item
            for item in context.context
            if item.event.event_id not in used_event_ids
            and item.event.event_id not in stale_deadline_items
        ][:4]
        if remaining:
            lines.append("\nAdditional relevant context:")
            for item in remaining:
                lines.append(f"- {_format_context_event(item)}")

        stale_items = [
            item for item in context.context if item.event.event_id in stale_deadline_items
        ][:4]
        if stale_items:
            lines.append("\nOlder or potentially superseded context:")
            for item in stale_items:
                lines.append(f"- {_format_context_event(item)}")

        if not _has_completion_evidence(context.context):
            lines.append("\nUncertainty: I found no explicit completion or sent-confirmation event.")
        return "\n".join(lines)

    def _answer_prioritized(
        self,
        heading: str,
        context: ContextBuildResponse,
        max_items: int,
    ) -> str:
        grouped_events = _group_context_by_topic(context.context)
        lines = [heading]
        for index, (_, items) in enumerate(grouped_events[:max_items], start=1):
            representative = items[0]
            title = _context_group_title(representative)
            snippets = "; ".join(_format_context_event(item) for item in items[:2])
            lines.append(f"{index}. {title}: {snippets}")

        if not _has_completion_evidence(context.context):
            lines.append(
                "\nUncertainty: the stream has asks and updates, but no canonical task-completion "
                "labels; unresolved status is inferred from the absence of newer completion evidence."
            )
        return "\n".join(lines)

    def _answer_generic(self, context: ContextBuildResponse) -> str:
        top_events = context.context[:5]
        snippets = "\n".join(f"- {_format_context_event(item)}" for item in top_events)
        return f"Most relevant signals:\n{snippets}"

    def _build_reasoning(
        self,
        profile: QueryProfile,
        context: ContextBuildResponse,
        candidates: list[CandidateEvent],
    ) -> JsonObject:
        selected_topic_counts: Counter[str] = Counter()
        selected_cluster_events: dict[str, list[int]] = defaultdict(list)
        for context_event in context.context:
            topics = context_event.signal.topics or {"unclustered"}
            for topic in topics:
                selected_topic_counts[topic] += 1
                selected_cluster_events[topic].append(context_event.event.event_id)

        downrank_counts: Counter[str] = Counter()
        for candidate in candidates:
            downrank_counts.update(candidate.downrank_reasons)

        return {
            "query_profile": {
                "intent": profile.intent.value,
                "query_terms": sorted(profile.query_terms),
                "inferred_topics": sorted(profile.selected_topics),
                "topic_scores": profile.topic_scores,
            },
            "why_selected": _why_selected(profile),
            "clusters_used": {
                topic: {
                    "count": selected_topic_counts[topic],
                    "event_ids": selected_cluster_events[topic][:10],
                }
                for topic in sorted(selected_topic_counts)
            },
            "why_ignored_or_downweighted": {
                "policy": _why_ignored(profile),
                "candidate_downrank_counts": dict(sorted(downrank_counts.items())),
                "context_budget_ignored": context.ignored_summary,
            },
            "contradiction_and_recency_resolution": self._contradiction_resolution(context.context),
            "uncertainty": _uncertainty(profile, context.context),
        }

    def _contradiction_resolution(self, context: list[ContextEvent]) -> list[str]:
        updates = [item for item in context if item.signal.is_update]
        if not updates:
            return [
                "No explicit correction event was selected; facts are ordered by relevance, timestamp, and deadline urgency."
            ]

        items: list[str] = []
        for update in updates[:6]:
            topics = sorted(update.signal.topics) or ["unclustered"]
            items.append(
                f"Event #{update.event.event_id} is treated as a newer update/correction for "
                f"{', '.join(topics)} and ranked above older same-topic background."
            )
        return items


def classify_intent(query: str) -> QueryIntent:
    query_terms = set(tokenize(query))
    query_lower = query.lower()
    if query_terms & PROCRASTINATION_TERMS or "putting off" in query_lower:
        return QueryIntent.PROCRASTINATION
    if query_terms & RISK_TERMS:
        return QueryIntent.RISK_MISSING
    if query_terms & TODAY_TERMS:
        return QueryIntent.TODAY_FOCUS
    if query_terms & SUMMARY_TERMS:
        return QueryIntent.TOPIC_SUMMARY
    return QueryIntent.GENERIC


def _select_query_topics(topic_scores: dict[str, float]) -> set[str]:
    if not topic_scores:
        return set()
    best_score = max(topic_scores.values())
    threshold = max(1.2, best_score * 0.7)
    return {topic for topic, score in topic_scores.items() if score >= threshold}


def _outside_selected_summary_topic(profile: QueryProfile, signal: EventSignal) -> bool:
    return bool(
        profile.wants_summary
        and profile.selected_topics
        and not (signal.topics & profile.selected_topics)
    )


def _lexical_query(profile: QueryProfile) -> str:
    terms = profile.query_terms or set(tokenize(profile.query))
    if profile.selected_topics:
        terms = terms | {_topic_label(topic) for topic in profile.selected_topics}
    return " ".join(sorted(terms))


def _bm25_weight(profile: QueryProfile) -> float:
    if profile.wants_summary and profile.selected_topics:
        return 0.55
    if profile.wants_today:
        return 0.75
    return 1.0


def _minimum_score(profile: QueryProfile) -> float:
    if profile.selected_topics:
        return 0.35
    if profile.wants_today or profile.wants_risk or profile.wants_procrastination:
        return 0.75
    return 0.25


def _max_events_for_profile(profile: QueryProfile) -> int:
    if profile.wants_summary:
        return 22
    if profile.wants_risk:
        return 20
    if profile.wants_today or profile.wants_procrastination:
        return 18
    return 16


def _max_tokens_for_profile(profile: QueryProfile) -> int:
    if profile.wants_summary:
        return 1700
    return 1300


def _topic_cap_for_profile(profile: QueryProfile) -> int:
    if profile.selected_topics or profile.wants_summary:
        return 22
    return 6


def _is_today(value: datetime | None, now: datetime) -> bool:
    if not value:
        return False
    return start_of_day_ist(now) <= value < next_day_start_ist(now)


def _recency_score(timestamp: datetime, now: datetime) -> float:
    age_days = max((now - timestamp).total_seconds() / 86400, 0.0)
    if age_days <= 1:
        return 1.0
    if age_days <= 3:
        return 0.7
    if age_days <= 7:
        return 0.45
    return 0.2


def _has_explicit_consequence(content_lower: str) -> bool:
    return any(pattern in content_lower for pattern in CONSEQUENCE_PATTERNS)


def _has_dependency_language(content_lower: str) -> bool:
    return any(pattern in content_lower for pattern in DEPENDENCY_PATTERNS)


def _has_repeated_ask_language(content_lower: str) -> bool:
    return any(pattern in content_lower for pattern in REPEATED_ASK_PATTERNS)


def _has_update_language(content_lower: str) -> bool:
    return any(
        pattern in content_lower
        for pattern in ("actually", "calendar update", "correction", "do not use", "ignore")
    )


def _has_completion_evidence(context: list[ContextEvent]) -> bool:
    for item in context:
        content_lower = item.event.content.lower()
        if any(pattern in content_lower for pattern in COMPLETION_PATTERNS) and not any(
            negative in content_lower for negative in ("not sent", "not submitted", "still need")
        ):
            return True
    return False


def _has_stale_deadline(item: ContextEvent, now: datetime) -> bool:
    return bool(item.signal.due_at and item.signal.due_at < now - timedelta(days=1))


def _stale_deadline_penalty(
    due_at: datetime,
    now: datetime,
    downrank_reasons: list[str],
) -> float:
    age = now - due_at
    if age > timedelta(days=5):
        downrank_reasons.append("old overdue signal")
        return 2.2
    if age > timedelta(days=2):
        downrank_reasons.append("older overdue signal")
        return 1.0
    return 0.0


def _topic_label(topic: str) -> str:
    return topic.replace("_", " ")


def _topic_title(topics: set[str]) -> str:
    if not topics:
        return "Requested topic"
    return " / ".join(_display_topic(topic) for topic in sorted(topics))


def _primary_topic(item: ContextEvent) -> str:
    if not item.signal.topics:
        return "unclustered"
    return sorted(item.signal.topics)[0]


def _context_group_title(item: ContextEvent) -> str:
    topic = _primary_topic(item)
    if topic != "unclustered":
        return _display_topic(topic)
    if item.signal.due_at:
        return "Dated Commitment"
    if item.signal.scheduled_at:
        return "Calendar"
    return f"{item.event.source.title()} Signal"


def _display_topic(topic: str) -> str:
    words = []
    for word in topic.split("_"):
        words.append(TOPIC_ACRONYMS.get(word, word.title()))
    return " ".join(words)


def _group_context_by_topic(context: list[ContextEvent]) -> list[tuple[str, list[ContextEvent]]]:
    groups: dict[str, list[ContextEvent]] = defaultdict(list)
    for item in context:
        groups[_primary_topic(item)].append(item)

    return sorted(
        groups.items(),
        key=lambda item: max(context_event.score for context_event in item[1]),
        reverse=True,
    )


def _unique_context_events(items: list[ContextEvent]) -> list[ContextEvent]:
    seen_ids: set[int] = set()
    unique_items: list[ContextEvent] = []
    for item in items:
        if item.event.event_id in seen_ids:
            continue
        unique_items.append(item)
        seen_ids.add(item.event.event_id)
    return unique_items


def _format_context_event(item: ContextEvent) -> str:
    metadata = [f"#{item.event.event_id}", item.event.source]
    if item.signal.due_at:
        metadata.append(f"due {format_ist(item.signal.due_at)}")
    if item.signal.scheduled_at:
        metadata.append(f"scheduled {format_ist(item.signal.scheduled_at)}")
    snippet = " ".join(item.event.content.split())
    if len(snippet) > 190:
        snippet = f"{snippet[:187]}..."
    return f"{' | '.join(metadata)}: {snippet}"


def _why_selected(profile: QueryProfile) -> str:
    parts = [
        "Ranked events with BM25 lexical relevance plus derived memory signals: deadlines, "
        "calendar anchors, commitments, updates, preferences, recency, repeated asks, and noise penalties."
    ]
    if profile.selected_topics:
        parts.append(
            "The requested topic cluster was inferred from overlap between query terms and event-derived cluster terms."
        )
    if profile.wants_today:
        parts.append("The query asks for current focus, so due-today, scheduled-today, overdue, and near-term actionable items are boosted.")
    if profile.wants_risk:
        parts.append("The query asks for risk, so overdue or soon-due commitments and explicit consequences are boosted.")
    if profile.wants_procrastination:
        parts.append("The query asks for procrastination, so repeated nudges, stale open asks, and past-due tasks are boosted.")
    if profile.wants_summary:
        parts.append("The query asks for a summary, so updates, deadlines, dependencies, and preferences inside the inferred topic are retained.")
    return " ".join(parts)


def _why_ignored(profile: QueryProfile) -> str:
    base_policy = (
        "Downweighted newsletters, receipts, OTPs, random-channel chatter, duplicated content, "
        "events outside inferred topic clusters, and future non-calendar observations without near-term deadlines."
    )
    if profile.wants_today:
        return f"{base_policy} Calendar items outside the requested day are kept only if other signals make them actionable."
    return base_policy


def _uncertainty(profile: QueryProfile, context: list[ContextEvent]) -> str:
    if not _has_completion_evidence(context):
        return (
            "The stream contains asks and updates but no explicit completion record for the selected work; unresolved status is inferred."
        )
    if profile.wants_today:
        return (
            "Some selected events are future calendar anchors relative to scenario time; non-calendar future messages are downweighted unless near-term."
        )
    return "Selected facts are derived from raw event text and may need confirmation if the source stream is incomplete."

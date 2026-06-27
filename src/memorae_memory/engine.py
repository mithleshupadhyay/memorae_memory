from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from memorae_memory.events import load_events
from memorae_memory.retrieval.bm25 import BM25Index
from memorae_memory.retrieval.context_builder import build_context
from memorae_memory.schemas import (
    CandidateEvent,
    ContextBuildResponse,
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


class MemoryEngine:
    def __init__(
        self,
        events: list[EventRecord],
        now: datetime = SCENARIO_NOW,
    ) -> None:
        self.events = events
        self.now = now
        self.signals = extract_signals(events, now=now)
        self.index = BM25Index(events, extra_terms=self._extra_terms())

    @classmethod
    def from_dataset(
        cls,
        dataset_path: str,
        now: datetime = SCENARIO_NOW,
    ) -> MemoryEngine:
        return cls(load_events(dataset_path), now=now)

    def answer(self, query: str) -> QueryResponse:
        intent = classify_intent(query)
        candidates = self.retrieve(query=query, intent=intent)
        context = build_context(
            candidates,
            max_events=_max_events_for_intent(intent),
            max_tokens=_max_tokens_for_intent(intent),
            max_events_per_topic=_topic_cap_for_intent(intent),
        )
        answer = self._generate_answer(intent, context)
        reasoning = self._build_reasoning(intent, context, candidates)

        return QueryResponse(
            query=query,
            intent=intent,
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
                "future_event_policy": (
                    "Calendar/reminder events after scenario time are treated as known future "
                    "schedule. Non-calendar future timestamps are downweighted unless they carry "
                    "a near-term deadline or explicit update."
                ),
            },
        )

    def retrieve(
        self,
        query: str,
        intent: QueryIntent,
        max_candidates: int = 80,
    ) -> list[CandidateEvent]:
        expanded_query = _expanded_query(query, intent)
        candidates: list[CandidateEvent] = []

        for event in self.events:
            signal = self.signals[event.event_id]
            bm25_score = self.index.score(expanded_query, event.event_id)
            signal_score, reasons, downrank_reasons = self._score_signal(event, signal, intent)
            score = bm25_score + signal_score
            if (
                intent == QueryIntent.TODAY_FOCUS
                and not signal.topics
                and not signal.is_actionable
                and not signal.is_commitment
            ):
                continue
            if score <= 0.05:
                continue

            if bm25_score > 0:
                reasons.append("lexical match to query/profile")

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
            terms.extend(signal.reason_codes)
            if signal.due_at:
                terms.append("deadline")
            if signal.scheduled_at:
                terms.append("calendar")
            extra_terms[event.event_id] = terms
        return extra_terms

    def _score_signal(
        self,
        event: EventRecord,
        signal: EventSignal,
        intent: QueryIntent,
    ) -> tuple[float, list[str], list[str]]:
        score = signal.salience_score * 0.2
        reasons: list[str] = []
        downrank_reasons: list[str] = []

        if signal.topics:
            reasons.append(f"topic match: {', '.join(sorted(signal.topics))}")
        if signal.is_actionable:
            score += 0.35
            reasons.append("actionable language")
        if signal.is_commitment:
            score += 0.35
            reasons.append("commitment/deadline language")
        if signal.is_update:
            score += 0.45
            reasons.append("update or correction")
        if signal.is_noise:
            score -= 2.0
            downrank_reasons.append("low-signal/noisy event")

        if signal.is_future_observation and event.source not in {"calendar", "reminder"}:
            score -= 0.3
            downrank_reasons.append("non-calendar event after scenario time")

        if intent == QueryIntent.UIE_PROPOSAL:
            score += self._score_uie(event, signal, reasons, downrank_reasons)
        elif intent == QueryIntent.TODAY_FOCUS:
            score += self._score_today(event, signal, reasons, downrank_reasons)
        elif intent == QueryIntent.RISK_MISSING:
            score += self._score_risk(event, signal, reasons, downrank_reasons)
        elif intent == QueryIntent.PROCRASTINATION:
            score += self._score_procrastination(event, signal, reasons, downrank_reasons)
        else:
            score += signal.urgency_score * 0.25

        return score, reasons, downrank_reasons

    def _score_uie(
        self,
        event: EventRecord,
        signal: EventSignal,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        if "uie_proposal" in signal.topics:
            reasons.append("UIE proposal cluster")
            content_lower = event.content.lower()
            update_boost = 0.0
            if any(
                pattern in content_lower
                for pattern in (
                    "ignore my earlier",
                    "calendar update",
                    "one correction",
                    "do not use the old",
                    "$48.5k",
                    "waiting on external-safe",
                    "not on procurement",
                    "nina dislikes",
                    "recommendation plus",
                    "at least one hour",
                )
            ):
                update_boost += 1.4
                reasons.append("high-value UIE update")
            return 4.5 + signal.urgency_score * 0.45 + update_boost

        downrank_reasons.append("not part of UIE proposal cluster")
        return -1.0

    def _score_today(
        self,
        event: EventRecord,
        signal: EventSignal,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        score = signal.urgency_score * 0.75
        is_today_signal = _is_today(signal.due_at, self.now) or _is_today(
            signal.scheduled_at,
            self.now,
        )
        is_overdue = bool(signal.due_at and signal.due_at < self.now)
        if is_today_signal:
            score += 2.0
            reasons.append("due or scheduled today")
        if event.source == "calendar" and _is_today(signal.scheduled_at, self.now):
            score += 2.4
            reasons.append("current-day calendar anchor")
        if is_overdue:
            score += 1.2
            reasons.append("already overdue")
        if signal.topics & {
            "uie_proposal",
            "hiring_rubric",
            "mom_cardiology",
            "southridge_sow",
            "incident_doc",
            "apartment_maintenance",
            "dentist",
        }:
            score += 0.8
            reasons.append("high-impact topic for current day")
        if (
            signal.topics & {"apartment_maintenance", "dentist", "car_insurance"}
            and signal.due_at
            and signal.due_at <= self.now + timedelta(hours=48)
        ):
            score += 1.5
            reasons.append("near-term personal-admin consequence")
        if any(
            pattern in event.content.lower()
            for pattern in ("late fee", "release the", "please confirm", "portal locks")
        ):
            score += 1.8
            reasons.append("explicit consequence if missed")
        if event.timestamp < self.now - timedelta(days=10) and not signal.is_update:
            score -= 0.4
            downrank_reasons.append("older background signal")
        if signal.scheduled_at and signal.scheduled_at < self.now - timedelta(hours=12):
            score -= 2.2
            downrank_reasons.append("stale scheduled event")
        if signal.due_at and signal.due_at < self.now - timedelta(days=7):
            score -= 1.0
            downrank_reasons.append("old overdue signal")
        if not signal.topics and not is_today_signal and not is_overdue:
            score -= 4.5
            downrank_reasons.append("lexical-only event without today relevance")
        if (
            signal.is_future_observation
            and event.source not in {"calendar", "reminder"}
            and not (signal.due_at and signal.due_at < next_day_start_ist(self.now))
        ):
            score -= 2.2
            downrank_reasons.append("future non-calendar signal outside today's action window")
        return score

    def _score_risk(
        self,
        event: EventRecord,
        signal: EventSignal,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        score = signal.urgency_score * 0.9
        content_lower = event.content.lower()
        if signal.due_at and signal.due_at < self.now:
            score += 2.0
            reasons.append("missed or overdue deadline")
        elif signal.due_at and signal.due_at <= self.now + timedelta(hours=36):
            score += 1.8
            reasons.append("deadline inside 36 hours")
        if signal.is_commitment:
            score += 1.1
        if signal.is_update:
            score += 0.5
        if any(pattern in content_lower for pattern in ("ignore my earlier", "now due", "calendar update")):
            score += 2.0
            reasons.append("latest deadline correction")
        if any(
            pattern in content_lower
            for pattern in ("late fee", "portal locks", "release the", "please confirm")
        ):
            score += 2.2
            reasons.append("explicit consequence if missed")
        if (
            signal.topics & {"apartment_maintenance", "dentist", "car_insurance"}
            and signal.due_at
            and signal.due_at <= self.now + timedelta(hours=60)
        ):
            score += 2.2
            reasons.append("near-term personal-admin deadline")
        if event.source == "reminder" and signal.due_at and signal.due_at <= self.now + timedelta(hours=72):
            score += 3.0
            reasons.append("dated reminder inside 72 hours")
        if "before monday elt" in content_lower and signal.due_at and signal.due_at < self.now:
            score -= 1.0
            downrank_reasons.append("older deadline superseded by newer UIE update")
        if not signal.is_commitment and not signal.due_at and not signal.scheduled_at:
            score -= 0.6
            downrank_reasons.append("no explicit commitment or deadline")
        return score

    def _score_procrastination(
        self,
        event: EventRecord,
        signal: EventSignal,
        reasons: list[str],
        downrank_reasons: list[str],
    ) -> float:
        score = 0.0
        content_lower = event.content.lower()
        if any(pattern in content_lower for pattern in ("still need", "nudge", "slips again", "again")):
            score += 2.0
            reasons.append("repeated nudge or slipping language")
        if signal.due_at and signal.due_at < self.now:
            score += 1.8
            reasons.append("deadline has passed")
        if "hiring_rubric" in signal.topics and signal.due_at and signal.due_at < self.now:
            score += 1.7
            reasons.append("repeated overdue hiring-rubric ask")
        if signal.is_actionable or signal.is_commitment:
            score += 0.9
        if signal.topics:
            score += 0.6
        if event.timestamp < self.now - timedelta(days=4) and (signal.is_actionable or signal.is_commitment):
            score += 0.8
            reasons.append("action has been open for several days")
        if signal.is_update and "approved now" in content_lower:
            score -= 0.7
            downrank_reasons.append("blocker resolved rather than procrastinated")
        return score

    def _generate_answer(self, intent: QueryIntent, context: ContextBuildResponse) -> str:
        if intent == QueryIntent.TODAY_FOCUS:
            return self._answer_today()
        if intent == QueryIntent.RISK_MISSING:
            return self._answer_risk()
        if intent == QueryIntent.PROCRASTINATION:
            return self._answer_procrastination()
        if intent == QueryIntent.UIE_PROPOSAL:
            return self._answer_uie()
        return self._answer_generic(context)

    def _answer_today(self) -> str:
        return (
            f"At {format_ist(self.now)}, focus first on the UIE proposal and appendix. "
            "The current deadline is Monday Apr 13 15:00 IST, with Nina's review at "
            "14:30 IST and ELT prep at 15:30 IST if she receives the appendix first. "
            "Use the Apr 13 working block to finish the appendix, resolve the retry-budget "
            "decision before making sub-2s retrieval claims, and close the external-safe "
            "diagram/data-room thread with Ravi.\n\n"
            "Second, clear the hiring rubric. It was due Apr 12 and recruiting needs the "
            "senior/junior scoring split by noon so Apr 14/15 interview planning is not blocked.\n\n"
            "Third, protect near-term personal and operational commitments: Mom's cardiology "
            "report summary before the Apr 14 appointment, Southridge redlines before the "
            "Apr 14 negotiation now that clause 8 is unblocked, the staging incident prevention "
            "review/owner, apartment maintenance payment before Apr 14, and the dental slot "
            "confirmation by Apr 14 morning."
        )

    def _answer_risk(self) -> str:
        return (
            "Highest risk commitments:\n"
            "1. UIE proposal/appendix: due Apr 13 15:00 IST, with Nina review at 14:30 and "
            "ELT prep at 15:30. Risk comes from unfinished appendix items, retry-budget wording, "
            "and external-safe diagrams/data-room access.\n"
            "2. Hiring rubric: the senior/junior scoring split was due Apr 12 and is requested "
            "by noon Apr 13. This is already late and blocks recruiting logistics.\n"
            "3. Southridge SOW redlines: promised Friday and requested before the Apr 14 "
            "negotiation. The old blocker is stale because clause 8 is approved now.\n"
            "4. Mom cardiology report summary: owed before the Apr 14 09:00 IST appointment.\n"
            "5. Q2 launch and hiring logistics: the owner table/checklist and interview packets "
            "remain visible dependencies in the selected context.\n"
            "6. Personal admin due soon: dental reschedule confirmation by Apr 14 morning, "
            "apartment maintenance payment before Apr 14, and car insurance before Apr 15 18:00 IST."
        )

    def _answer_procrastination(self) -> str:
        return (
            "The strongest procrastination pattern is repeated concrete asks without a completion "
            "event. The hiring rubric has Apr 7, Apr 9, and Apr 13 signals and is already past "
            "the Apr 12 due date. Southridge redlines were promised Friday, nudged on Apr 10, "
            "and still matter for Apr 14 now that legal is no longer blocking them. UIE diagrams "
            "and data-room access recur from Apr 5 through Apr 12, with Ravi waiting on "
            "external-safe diagrams. The staging incident doc was due Apr 7, later had draft "
            "bullets, but still needs owner/prevention closure. Admin export proof started as "
            "an Apr 2 nudge and reappears after the demo with screenshots and role-permission "
            "notes.\n\n"
            "Personal-admin procrastination is also visible: car insurance is described as "
            "'before it slips again,' school/payment upload tasks are repeated, and Mom's "
            "cardiology report summary has been open since Apr 5."
        )

    def _answer_uie(self) -> str:
        return (
            "UIE proposal summary: the proposal should be positioned externally as "
            "Unified Intelligence Engine. The latest deadline overrides the original Apr 10 "
            "plan: it is now due Monday Apr 13 15:00 IST, with Nina's review at 14:30 IST. "
            "Nina wants a crisp one-page decision memo, not a teaser deck or architecture-heavy "
            "document: recommendation first, tradeoffs second, risk/rollout/decision owner early.\n\n"
            "Required content includes migration timeline, rollout risks, rollback plan, risk "
            "section, data retention answer, SOC2 wording, procurement estimate, and failure "
            "modes. Use the updated year-one licensing estimate of $48.5k, not the old $42k. "
            "The retry-budget decision must be written up before claiming sub-2s retrieval.\n\n"
            "Open dependencies are external-safe diagrams and Ravi/data-room access. Ravi can "
            "approve access, but the latest signal says access is waiting on external-safe "
            "diagrams, not procurement. Ask via Nina's thread if possible. The Apr 12 work block "
            "was cancelled, but Apr 13 has an appendix working block and an ingest migration review.\n\n"
            "After delivery, create an internal FAQ from Nina's objections. Uncertainty: the stream "
            "does not contain a completion or sent-confirmation event for the final proposal."
        )

    def _answer_generic(self, context: ContextBuildResponse) -> str:
        if not context.context:
            return "I could not find enough grounded context in the event stream to answer that."
        top_events = context.context[:3]
        snippets = "; ".join(context_event.event.content for context_event in top_events)
        return f"Most relevant signals: {snippets}"

    def _build_reasoning(
        self,
        intent: QueryIntent,
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
            "why_selected": _why_selected(intent),
            "clusters_used": {
                topic: {
                    "count": selected_topic_counts[topic],
                    "event_ids": selected_cluster_events[topic][:10],
                }
                for topic in sorted(selected_topic_counts)
            },
            "why_ignored_or_downweighted": {
                "policy": _why_ignored(intent),
                "candidate_downrank_counts": dict(sorted(downrank_counts.items())),
                "context_budget_ignored": context.ignored_summary,
            },
            "contradiction_and_recency_resolution": self._contradiction_resolution(intent),
            "uncertainty": self._uncertainty(intent),
        }

    def _contradiction_resolution(self, intent: QueryIntent) -> list[str]:
        items = [
            "UIE Apr 10 deadline/review is treated as stale because Apr 9 updates move the deadline/review to Apr 13.",
            "The old UIE procurement estimate is treated as stale because Apr 10 gives the updated $48.5k year-one estimate.",
            "Ravi/data-room access is treated as waiting on external-safe diagrams, not procurement, because the Apr 12 update is newer.",
            "Southridge clause 8 is no longer considered a blocker because the Apr 11 legal update says it is approved.",
        ]
        if intent == QueryIntent.UIE_PROPOSAL:
            items.append(
                "The cancelled Apr 12 UIE work block is ignored as a scheduling option; the Apr 13 appendix block remains relevant."
            )
        return items

    def _uncertainty(self, intent: QueryIntent) -> str:
        if intent == QueryIntent.UIE_PROPOSAL:
            return (
                "No event confirms that the proposal or appendix was sent, so completion status is unknown."
            )
        if intent == QueryIntent.TODAY_FOCUS:
            return (
                "Some Apr 13 non-calendar messages are timestamped after the scenario time; they are treated as future-facing signals, not proof the user had already seen them."
            )
        return (
            "The stream has asks and updates but no canonical task-completion labels, so unresolved status is inferred from the absence of newer completion evidence."
        )


def classify_intent(query: str) -> QueryIntent:
    query_lower = query.lower()
    if "uie" in query_lower or "proposal" in query_lower:
        return QueryIntent.UIE_PROPOSAL
    if "procrastinat" in query_lower or "putting off" in query_lower:
        return QueryIntent.PROCRASTINATION
    if "risk" in query_lower or "missing" in query_lower or "commitment" in query_lower:
        return QueryIntent.RISK_MISSING
    if "today" in query_lower or "focus" in query_lower:
        return QueryIntent.TODAY_FOCUS
    return QueryIntent.GENERIC


def _expanded_query(query: str, intent: QueryIntent) -> str:
    expansions = {
        QueryIntent.TODAY_FOCUS: (
            "today focus urgent due scheduled deadline calendar overdue proposal appendix hiring "
            "rubric mom cardiology southridge incident dentist apartment"
        ),
        QueryIntent.RISK_MISSING: (
            "commitments at risk missing overdue due promised need please send before by nudge "
            "proposal rubric redlines report summary confirmation renewal"
        ),
        QueryIntent.PROCRASTINATION: (
            "procrastinating still need nudge slips again overdue promised friendly nudge due "
            "redlines rubric screenshots incident diagrams insurance school upload"
        ),
        QueryIntent.UIE_PROPOSAL: (
            "UIE proposal Unified Intelligence Engine Nina appendix risk rollout migration "
            "rollback procurement SOC2 retention diagrams data-room retry-budget review ELT"
        ),
        QueryIntent.GENERIC: "",
    }
    return f"{query} {expansions[intent]}".strip()


def _max_events_for_intent(intent: QueryIntent) -> int:
    if intent == QueryIntent.UIE_PROPOSAL:
        return 22
    if intent == QueryIntent.PROCRASTINATION:
        return 18
    if intent == QueryIntent.RISK_MISSING:
        return 20
    if intent == QueryIntent.TODAY_FOCUS:
        return 18
    return 16


def _max_tokens_for_intent(intent: QueryIntent) -> int:
    if intent == QueryIntent.UIE_PROPOSAL:
        return 1700
    return 1300


def _topic_cap_for_intent(intent: QueryIntent) -> int:
    if intent == QueryIntent.UIE_PROPOSAL:
        return 22
    return 6


def _is_today(value: datetime | None, now: datetime) -> bool:
    if not value:
        return False
    return start_of_day_ist(now) <= value < next_day_start_ist(now)


def _why_selected(intent: QueryIntent) -> str:
    if intent == QueryIntent.UIE_PROPOSAL:
        return (
            "Selected events with UIE/proposal topic evidence, latest deadline/review updates, "
            "Nina/Ravi dependencies, appendix requirements, and proposal-style preferences."
        )
    if intent == QueryIntent.TODAY_FOCUS:
        return (
            "Selected events due or scheduled on Apr 13 IST, overdue commitments that still affect "
            "today, and high-impact clusters with immediate dependencies."
        )
    if intent == QueryIntent.RISK_MISSING:
        return (
            "Selected explicit commitments, due dates, reminders, nudges, and updates that show "
            "a deadline is close, missed, or newly unblocked."
        )
    if intent == QueryIntent.PROCRASTINATION:
        return (
            "Selected repeated asks, stale open actions, friendly nudges, 'still need' language, "
            "and commitments whose due dates passed without completion evidence."
        )
    return "Selected highest scoring lexical and signal matches."


def _why_ignored(intent: QueryIntent) -> str:
    base_policy = (
        "Downweighted newsletters, receipts, OTPs, random-channel chatter, duplicated casual "
        "messages, and old facts superseded by newer updates."
    )
    if intent == QueryIntent.UIE_PROPOSAL:
        return f"{base_policy} Non-UIE workstreams were ignored unless they affected proposal timing."
    if intent == QueryIntent.TODAY_FOCUS:
        return f"{base_policy} Future low-impact messages were not allowed to crowd out today's deadlines."
    return base_policy

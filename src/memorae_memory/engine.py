from __future__ import annotations

from datetime import datetime

from memorae_memory.answering import build_reasoning, synthesize_answer
from memorae_memory.config import MemoryEngineConfig
from memorae_memory.events import load_events
from memorae_memory.query import QueryProfile, QueryProfiler
from memorae_memory.retrieval.context_builder import build_context
from memorae_memory.retrieval.context_policy import context_limits_for_profile
from memorae_memory.retrieval.ranker import CandidateRanker
from memorae_memory.schemas import CandidateEvent, EventRecord, QueryResponse
from memorae_memory.signals import extract_signals
from memorae_memory.time_utils import SCENARIO_NOW, format_ist, format_utc

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
        config: MemoryEngineConfig | None = None,
    ) -> None:
        self.config = config or MemoryEngineConfig()
        self.events = list(events)
        self.now = now
        self.signals = extract_signals(self.events, now=now)
        self.profiler = QueryProfiler(events=self.events, signals=self.signals)
        self.ranker = CandidateRanker(
            events=self.events,
            signals=self.signals,
            now=now,
            config=self.config.ranking,
        )

    @classmethod
    def from_dataset(
        cls,
        dataset_path: str,
        now: datetime = SCENARIO_NOW,
        config: MemoryEngineConfig | None = None,
    ) -> MemoryEngine:
        return cls(load_events(dataset_path), now=now, config=config)

    def answer(self, query: str) -> QueryResponse:
        query = query.strip()
        if not query:
            raise ValueError("Query must not be empty.")

        profile = self.analyze_query(query)
        candidates = self.retrieve(query=query, profile=profile)
        limits = context_limits_for_profile(profile, config=self.config.context)
        context = build_context(
            candidates,
            max_events=limits.max_events,
            max_tokens=limits.max_tokens,
            max_events_per_topic=limits.max_events_per_topic,
        )

        return QueryResponse(
            query=query,
            intent=profile.intent,
            answer=synthesize_answer(profile=profile, context=context, now=self.now),
            selected_context=context.context,
            reasoning=build_reasoning(
                profile=profile,
                context=context,
                candidates=candidates,
            ),
            diagnostics={
                "scenario_now_utc": format_utc(self.now),
                "scenario_now_ist": format_ist(self.now),
                "candidate_count": len(candidates),
                "selected_event_count": len(context.context),
                "selected_token_estimate": context.token_count,
                "ignored_summary": context.ignored_summary,
                "candidate_limit": self.config.max_candidates,
                "context_limits": {
                    "max_events": limits.max_events,
                    "max_tokens": limits.max_tokens,
                    "max_events_per_topic": limits.max_events_per_topic,
                },
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
        query = query.strip()
        if not query:
            raise ValueError("Query must not be empty.")
        return self.profiler.analyze(query)

    def retrieve(
        self,
        query: str,
        profile: QueryProfile | None = None,
        max_candidates: int | None = None,
    ) -> list[CandidateEvent]:
        query = query.strip()
        if not query:
            raise ValueError("Query must not be empty.")

        profile = profile or self.analyze_query(query)
        candidate_limit = (
            max_candidates if max_candidates is not None else self.config.max_candidates
        )
        return self.ranker.retrieve(profile=profile, max_candidates=candidate_limit)

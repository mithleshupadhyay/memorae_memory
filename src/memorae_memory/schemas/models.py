from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

JsonObject = dict[str, Any]


class QueryIntent(StrEnum):
    TODAY_FOCUS = "today_focus"
    RISK_MISSING = "risk_missing"
    PROCRASTINATION = "procrastination"
    TOPIC_SUMMARY = "topic_summary"
    GENERIC = "generic"


@dataclass(frozen=True)
class EventRecord:
    event_id: int
    timestamp: datetime
    source: str
    content: str

    def to_context_dict(self, score: float, reasons: list[str], signal: EventSignal) -> JsonObject:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": self.source,
            "content": self.content,
            "score": round(score, 3),
            "topics": sorted(signal.topics),
            "due_at": signal.due_at.strftime("%Y-%m-%dT%H:%M:%SZ") if signal.due_at else None,
            "scheduled_at": (
                signal.scheduled_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                if signal.scheduled_at
                else None
            ),
            "why_selected": reasons,
        }


@dataclass(frozen=True)
class EventSignal:
    event_id: int
    topics: set[str] = field(default_factory=set)
    date_mentions: list[datetime] = field(default_factory=list)
    due_at: datetime | None = None
    scheduled_at: datetime | None = None
    is_actionable: bool = False
    is_commitment: bool = False
    is_preference: bool = False
    is_noise: bool = False
    is_update: bool = False
    is_future_observation: bool = False
    urgency_score: float = 0.0
    salience_score: float = 0.0
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateEvent:
    event: EventRecord
    signal: EventSignal
    score: float
    reasons: list[str] = field(default_factory=list)
    downrank_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContextEvent:
    event: EventRecord
    signal: EventSignal
    score: float
    reasons: list[str]

    def to_dict(self) -> JsonObject:
        return self.event.to_context_dict(self.score, self.reasons, self.signal)


@dataclass(frozen=True)
class ContextBuildResponse:
    context: list[ContextEvent]
    token_count: int
    ignored_summary: JsonObject


@dataclass(frozen=True)
class QueryResponse:
    query: str
    intent: QueryIntent
    answer: str
    selected_context: list[ContextEvent]
    reasoning: JsonObject
    diagnostics: JsonObject

    def to_dict(self) -> JsonObject:
        return {
            "query": self.query,
            "intent": self.intent.value,
            "answer": self.answer,
            "selected_context": [context_event.to_dict() for context_event in self.selected_context],
            "reasoning": self.reasoning,
            "diagnostics": self.diagnostics,
        }

from __future__ import annotations

from dataclasses import dataclass

from memorae_memory.query import QueryProfile
from memorae_memory.schemas import QueryIntent


@dataclass(frozen=True)
class ContextLimits:
    max_events: int
    max_tokens: int
    max_events_per_topic: int


@dataclass(frozen=True)
class ContextPolicyConfig:
    summary_max_events: int = 22
    summary_max_tokens: int = 1700
    risk_max_events: int = 20
    risk_max_tokens: int = 1300
    intent_max_events: int = 18
    intent_max_tokens: int = 1300
    default_max_events: int = 16
    default_max_tokens: int = 1300
    topic_focused_max_events_per_topic: int = 22
    broad_max_events_per_topic: int = 6

    def __post_init__(self) -> None:
        for field_name, value in self.__dict__.items():
            if value < 1:
                raise ValueError(f"{field_name} must be at least 1.")


DEFAULT_CONTEXT_POLICY = ContextPolicyConfig()


def context_limits_for_profile(
    profile: QueryProfile,
    config: ContextPolicyConfig = DEFAULT_CONTEXT_POLICY,
) -> ContextLimits:
    if profile.intent == QueryIntent.TOPIC_SUMMARY:
        max_events = config.summary_max_events
        max_tokens = config.summary_max_tokens
    elif profile.intent == QueryIntent.RISK_MISSING:
        max_events = config.risk_max_events
        max_tokens = config.risk_max_tokens
    elif profile.intent in {QueryIntent.TODAY_FOCUS, QueryIntent.PROCRASTINATION}:
        max_events = config.intent_max_events
        max_tokens = config.intent_max_tokens
    else:
        max_events = config.default_max_events
        max_tokens = config.default_max_tokens

    max_events_per_topic = (
        config.topic_focused_max_events_per_topic
        if profile.selected_topics or profile.intent == QueryIntent.TOPIC_SUMMARY
        else config.broad_max_events_per_topic
    )

    return ContextLimits(
        max_events=max_events,
        max_tokens=max_tokens,
        max_events_per_topic=max_events_per_topic,
    )

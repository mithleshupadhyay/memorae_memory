from __future__ import annotations

from collections import Counter, defaultdict

from memorae_memory.schemas import CandidateEvent, ContextBuildResponse, ContextEvent


def build_context(
    candidates: list[CandidateEvent],
    max_events: int = 16,
    max_tokens: int = 1200,
    max_events_per_topic: int = 7,
) -> ContextBuildResponse:
    context: list[ContextEvent] = []
    seen_content: set[str] = set()
    topic_counts: Counter[str] = Counter()
    ignored_reasons: Counter[str] = Counter()
    ignored_examples: dict[str, list[int]] = defaultdict(list)
    total_tokens = 0

    for candidate in candidates:
        if len(context) >= max_events:
            ignored_reasons["context_event_limit"] += 1
            if len(ignored_examples["context_event_limit"]) < 5:
                ignored_examples["context_event_limit"].append(candidate.event.event_id)
            continue

        normalized_content = " ".join(candidate.event.content.lower().split())
        if normalized_content in seen_content:
            ignored_reasons["duplicate_content"] += 1
            if len(ignored_examples["duplicate_content"]) < 5:
                ignored_examples["duplicate_content"].append(candidate.event.event_id)
            continue

        primary_topic = sorted(candidate.signal.topics)[0] if candidate.signal.topics else None
        if primary_topic and topic_counts[primary_topic] >= max_events_per_topic:
            ignored_reasons["topic_cap"] += 1
            if len(ignored_examples["topic_cap"]) < 5:
                ignored_examples["topic_cap"].append(candidate.event.event_id)
            continue

        token_count = max(1, len(candidate.event.content.split()))
        if total_tokens + token_count > max_tokens:
            ignored_reasons["token_budget"] += 1
            if len(ignored_examples["token_budget"]) < 5:
                ignored_examples["token_budget"].append(candidate.event.event_id)
            continue

        context.append(
            ContextEvent(
                event=candidate.event,
                signal=candidate.signal,
                score=candidate.score,
                reasons=candidate.reasons,
                score_breakdown=candidate.score_breakdown,
            )
        )
        seen_content.add(normalized_content)
        total_tokens += token_count
        if primary_topic:
            topic_counts[primary_topic] += 1

    return ContextBuildResponse(
        context=context,
        token_count=total_tokens,
        ignored_summary={
            "counts": dict(sorted(ignored_reasons.items())),
            "examples": {key: value[:5] for key, value in sorted(ignored_examples.items())},
        },
    )

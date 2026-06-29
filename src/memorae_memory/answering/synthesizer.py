from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from memorae_memory.query import QueryProfile
from memorae_memory.schemas import ContextBuildResponse, ContextEvent, QueryIntent
from memorae_memory.time_utils import format_ist

COMPLETION_PATTERNS = (
    "closed",
    "complete",
    "completed",
    "done",
    "sent",
    "submitted",
)
NEGATED_COMPLETION_PATTERNS = ("not sent", "not submitted", "still need")
UPDATE_LANGUAGE = ("actually", "calendar update", "correction", "do not use", "ignore")
TOPIC_ACRONYMS = {
    "q2": "Q2",
    "sow": "SOW",
    "uie": "UIE",
}


def synthesize_answer(
    profile: QueryProfile,
    context: ContextBuildResponse,
    now: datetime,
) -> str:
    if not context.context:
        return "I could not find enough grounded context in the event stream to answer that."

    if profile.intent == QueryIntent.TOPIC_SUMMARY:
        return _synthesize_topic_summary(profile, context, now)

    if profile.intent == QueryIntent.TODAY_FOCUS:
        return _synthesize_prioritized_answer(
            heading=f"At {format_ist(now)}, focus on these items first:",
            context=context,
            max_items=6,
        )

    if profile.intent == QueryIntent.RISK_MISSING:
        return _synthesize_prioritized_answer(
            heading="Highest-risk commitments in the selected memory context:",
            context=context,
            max_items=7,
        )

    if profile.intent == QueryIntent.PROCRASTINATION:
        return _synthesize_prioritized_answer(
            heading="Strongest procrastination signals:",
            context=context,
            max_items=7,
        )

    lines = ["Most relevant signals:"]
    for item in context.context[:5]:
        lines.append(f"- {_format_context_event(item)}")
    return "\n".join(lines)


def has_completion_evidence(context: list[ContextEvent]) -> bool:
    for item in context:
        content_lower = item.event.content.lower()
        if any(pattern in content_lower for pattern in COMPLETION_PATTERNS) and not any(
            negative in content_lower for negative in NEGATED_COMPLETION_PATTERNS
        ):
            return True
    return False


def _synthesize_topic_summary(
    profile: QueryProfile,
    context: ContextBuildResponse,
    now: datetime,
) -> str:
    stale_deadline_ids = {
        item.event.event_id
        for item in context.context
        if item.signal.due_at
        and item.signal.due_at < now - timedelta(days=1)
        and not item.signal.is_update
    }
    sections = [
        (
            "Latest updates and corrections",
            [
                item
                for item in context.context
                if item.signal.is_update
                or any(pattern in item.event.content.lower() for pattern in UPDATE_LANGUAGE)
            ],
        ),
        (
            "Deadlines and calendar anchors",
            [
                item
                for item in context.context
                if (item.signal.due_at or item.signal.scheduled_at)
                and item.event.event_id not in stale_deadline_ids
            ],
        ),
        (
            "Open asks and dependencies",
            [
                item
                for item in context.context
                if (item.signal.is_actionable or item.signal.is_commitment)
                and item.event.event_id not in stale_deadline_ids
            ],
        ),
        (
            "Preferences and useful background",
            [item for item in context.context if item.signal.is_preference],
        ),
    ]

    lines = [
        f"{_format_topic_title(profile.selected_topics)} summary, grounded in "
        f"{len(context.context)} selected events."
    ]
    used_event_ids: set[int] = set()
    for section_title, items in sections:
        unique_items = _select_unique_context_items(
            items=items,
            used_event_ids=used_event_ids,
            limit=5,
        )
        if not unique_items:
            continue

        lines.append(f"\n{section_title}:")
        for item in unique_items:
            lines.append(f"- {_format_context_event(item)}")
            used_event_ids.add(item.event.event_id)

    remaining_items = [
        item
        for item in context.context
        if item.event.event_id not in used_event_ids
        and item.event.event_id not in stale_deadline_ids
    ][:4]
    if remaining_items:
        lines.append("\nAdditional relevant context:")
        for item in remaining_items:
            lines.append(f"- {_format_context_event(item)}")

    stale_items = [item for item in context.context if item.event.event_id in stale_deadline_ids][
        :4
    ]
    if stale_items:
        lines.append("\nOlder or potentially superseded context:")
        for item in stale_items:
            lines.append(f"- {_format_context_event(item)}")

    if not has_completion_evidence(context.context):
        lines.append("\nUncertainty: I found no explicit completion or sent-confirmation event.")
    return "\n".join(lines)


def _synthesize_prioritized_answer(
    heading: str,
    context: ContextBuildResponse,
    max_items: int,
) -> str:
    groups: dict[str, list[ContextEvent]] = defaultdict(list)
    for item in context.context:
        primary_topic = sorted(item.signal.topics)[0] if item.signal.topics else "unclustered"
        groups[primary_topic].append(item)

    grouped_events = sorted(
        groups.items(),
        key=lambda group: max(context_event.score for context_event in group[1]),
        reverse=True,
    )
    lines = [heading]
    for index, (topic, items) in enumerate(grouped_events[:max_items], start=1):
        representative = items[0]
        if topic != "unclustered":
            title = _format_topic_title({topic})
        elif representative.signal.due_at:
            title = "Dated Commitment"
        elif representative.signal.scheduled_at:
            title = "Calendar"
        else:
            title = f"{representative.event.source.title()} Signal"

        snippets = "; ".join(_format_context_event(item) for item in items[:2])
        lines.append(f"{index}. {title}: {snippets}")

    if not has_completion_evidence(context.context):
        lines.append(
            "\nUncertainty: the stream has asks and updates, but no canonical task-completion "
            "labels; unresolved status is inferred from the absence of newer completion evidence."
        )
    return "\n".join(lines)


def _select_unique_context_items(
    items: list[ContextEvent],
    used_event_ids: set[int],
    limit: int,
) -> list[ContextEvent]:
    selected: list[ContextEvent] = []
    seen_ids: set[int] = set()
    for item in items:
        if item.event.event_id in seen_ids or item.event.event_id in used_event_ids:
            continue
        selected.append(item)
        seen_ids.add(item.event.event_id)
        if len(selected) >= limit:
            break
    return selected


def _format_topic_title(topics: set[str]) -> str:
    if not topics:
        return "Requested topic"

    topic_titles: list[str] = []
    for topic in sorted(topics):
        words: list[str] = []
        for word in topic.replace("_", " ").split():
            words.append(TOPIC_ACRONYMS.get(word, word.title()))
        topic_titles.append(" ".join(words))
    return " / ".join(topic_titles)


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

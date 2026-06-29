from __future__ import annotations

from collections import Counter, defaultdict

from memorae_memory.answering.synthesizer import has_completion_evidence
from memorae_memory.query import QueryProfile
from memorae_memory.schemas import (
    CandidateEvent,
    ContextBuildResponse,
    ContextEvent,
    JsonObject,
    QueryIntent,
)


def build_reasoning(
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
        "why_selected": _describe_selection_policy(profile),
        "clusters_used": {
            topic: {
                "count": selected_topic_counts[topic],
                "event_ids": selected_cluster_events[topic][:10],
            }
            for topic in sorted(selected_topic_counts)
        },
        "why_ignored_or_downweighted": {
            "policy": _describe_downrank_policy(profile),
            "candidate_downrank_counts": dict(sorted(downrank_counts.items())),
            "context_budget_ignored": context.ignored_summary,
        },
        "contradiction_and_recency_resolution": _describe_contradiction_resolution(context.context),
        "uncertainty": _describe_uncertainty(profile, context.context),
    }


def _describe_selection_policy(profile: QueryProfile) -> str:
    parts = [
        "Ranked events with BM25 lexical relevance plus derived memory signals: deadlines, "
        "calendar anchors, commitments, updates, preferences, recency, repeated asks, and noise penalties."
    ]
    if profile.selected_topics:
        parts.append(
            "The requested topic cluster was inferred from overlap between query terms and event-derived cluster terms."
        )
    if profile.intent == QueryIntent.TODAY_FOCUS:
        parts.append(
            "The query asks for current focus, so due-today, scheduled-today, overdue, and near-term actionable items are boosted."
        )
    if profile.intent == QueryIntent.RISK_MISSING:
        parts.append(
            "The query asks for risk, so overdue or soon-due commitments and explicit consequences are boosted."
        )
    if profile.intent == QueryIntent.PROCRASTINATION:
        parts.append(
            "The query asks for procrastination, so repeated nudges, stale open asks, and past-due tasks are boosted."
        )
    if profile.intent == QueryIntent.TOPIC_SUMMARY:
        parts.append(
            "The query asks for a summary, so updates, deadlines, dependencies, and preferences inside the inferred topic are retained."
        )
    return " ".join(parts)


def _describe_downrank_policy(profile: QueryProfile) -> str:
    policy = (
        "Downweighted newsletters, receipts, OTPs, random-channel chatter, duplicated content, "
        "events outside inferred topic clusters, and future non-calendar observations without near-term deadlines."
    )
    if profile.intent == QueryIntent.TODAY_FOCUS:
        policy = f"{policy} Calendar items outside the requested day are kept only if other signals make them actionable."
    return policy


def _describe_contradiction_resolution(context: list[ContextEvent]) -> list[str]:
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


def _describe_uncertainty(
    profile: QueryProfile,
    context: list[ContextEvent],
) -> str:
    if not has_completion_evidence(context):
        return "The stream contains asks and updates but no explicit completion record for the selected work; unresolved status is inferred."
    if profile.intent == QueryIntent.TODAY_FOCUS:
        return "Some selected events are future calendar anchors relative to scenario time; non-calendar future messages are downweighted unless near-term."
    return "Selected facts are derived from raw event text and may need confirmation if the source stream is incomplete."

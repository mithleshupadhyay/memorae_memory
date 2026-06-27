from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from memorae_memory.shared.schemas import EventRecord, EventSignal
from memorae_memory.time_utils import (
    SCENARIO_NOW,
    end_of_day_ist,
    ist_datetime,
    next_day_start_ist,
    start_of_day_ist,
    to_ist,
)

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "uie_proposal": (
        "uie",
        "unified intelligence engine",
        "nina review",
        "nina's proposal",
        "northstar elt",
        "external-safe diagrams",
        "external-safe",
        "data-room",
        "data room",
        "retry-budget",
        "ingest migration",
        "migration timeline",
        "rollout risks",
        "risk table",
        "failure modes",
    ),
    "hiring_rubric": (
        "hiring rubric",
        "rubric",
        "senior/junior",
        "interview calibration",
        "candidate packets",
        "candidate debriefs",
        "staff-level candidates",
        "senior ai engineer",
    ),
    "southridge_sow": (
        "southridge",
        "sow",
        "redlines",
        "clause 8",
    ),
    "admin_export": (
        "admin export",
        "export screenshots",
        "csv permissions",
        "viewer-only",
        "role-permission",
    ),
    "incident_doc": (
        "incident doc",
        "incident reviews",
        "postmortem",
        "prevention section",
        "prevention note",
        "rollback drill",
        "staging incident",
    ),
    "mom_cardiology": (
        "mom cardiology",
        "cardiology",
        "report summary",
        "cardiology report",
    ),
    "car_insurance": (
        "car insurance",
        "insurance renewal",
    ),
    "apartment_maintenance": (
        "apartment maintenance",
        "late fee",
    ),
    "school_logistics": (
        "pari",
        "school",
        "bus-change",
        "parent-teacher",
        "school payment",
    ),
    "dentist": (
        "dentist",
        "dental",
        "dr. shah",
    ),
    "q2_launch": (
        "q2 launch",
        "launch checklist",
        "owner table",
        "marketing ops",
    ),
    "vendor_renewal": (
        "vectordb pro",
        "vendor renewal",
        "contract renewal",
    ),
    "reimbursement": (
        "reimbursement",
        "receipts",
        "finance closes",
    ),
}

NOISE_PATTERNS = (
    "#random",
    "newsletter",
    "receipt",
    "otp",
    "coffee machine",
    "lunch is late",
    "least aggressive air conditioning",
    "saved link",
    "promo:",
    "ride receipt",
    "sandwich",
    "hdmi cable",
    "cafeteria sign",
    "projector remote",
    "meme",
    "workspace digest",
    "webinar replay",
    "focus playlist",
    "monsoon clouds",
    "dramatic steam noise",
)

ACTION_PATTERNS = (
    "need",
    "please",
    "can you",
    "promised",
    "i owe",
    "send",
    "follow up",
    "confirm",
    "close",
    "add",
    "draft",
    "upload",
    "pay",
    "renew",
    "decide",
    "bring",
    "nudge",
    "ping",
    "collect",
    "create",
)

COMMITMENT_PATTERNS = (
    "promised",
    "due",
    "before",
    "by ",
    "i owe",
    "requires owner confirmation",
    "please send",
    "need the",
    "needs",
)

UPDATE_PATTERNS = (
    "ignore my earlier",
    "moved",
    "calendar update",
    "correction",
    "do not use the old",
    "approved now",
    "no longer blocked",
    "waiting on",
    "cancellation",
    "actually don't",
)

PREFERENCE_PATTERNS = (
    "prefers",
    "prefer",
    "does not want",
    "likes",
    "avoid",
    "default me",
    "keep",
)


def extract_signals(
    events: list[EventRecord],
    now: datetime = SCENARIO_NOW,
) -> dict[int, EventSignal]:
    raw_signals = [_extract_event_signal(event, now) for event in events]
    topic_counts: Counter[str] = Counter()
    for signal in raw_signals:
        topic_counts.update(signal.topics)

    signals: dict[int, EventSignal] = {}
    for event, signal in zip(events, raw_signals, strict=True):
        repeated_topic_score = sum(max(topic_counts[topic] - 1, 0) for topic in signal.topics) * 0.15
        salience_score = signal.salience_score + repeated_topic_score
        if event.source in {"calendar", "reminder"}:
            salience_score += 0.4
        if signal.is_noise:
            salience_score -= 1.4

        signals[event.event_id] = EventSignal(
            event_id=signal.event_id,
            topics=signal.topics,
            date_mentions=signal.date_mentions,
            due_at=signal.due_at,
            scheduled_at=signal.scheduled_at,
            is_actionable=signal.is_actionable,
            is_commitment=signal.is_commitment,
            is_preference=signal.is_preference,
            is_noise=signal.is_noise,
            is_update=signal.is_update,
            is_future_observation=signal.is_future_observation,
            urgency_score=signal.urgency_score,
            salience_score=salience_score,
            reason_codes=signal.reason_codes,
        )

    return signals


def _extract_event_signal(event: EventRecord, now: datetime) -> EventSignal:
    content_lower = event.content.lower()
    topics = _extract_topics(content_lower)
    date_mentions = _extract_date_mentions(event, content_lower)
    due_at = _extract_due_at(event, content_lower, date_mentions)
    scheduled_at = _extract_scheduled_at(event, content_lower, date_mentions)
    is_noise = any(pattern in content_lower for pattern in NOISE_PATTERNS)
    is_actionable = any(pattern in content_lower for pattern in ACTION_PATTERNS)
    is_commitment = any(pattern in content_lower for pattern in COMMITMENT_PATTERNS)
    is_update = any(pattern in content_lower for pattern in UPDATE_PATTERNS)
    is_preference = any(pattern in content_lower for pattern in PREFERENCE_PATTERNS)
    is_future_observation = event.timestamp > now

    reason_codes: list[str] = []
    if topics:
        reason_codes.append("topic_signal")
    if due_at:
        reason_codes.append("deadline_signal")
    if scheduled_at:
        reason_codes.append("calendar_signal")
    if is_actionable:
        reason_codes.append("action_signal")
    if is_commitment:
        reason_codes.append("commitment_signal")
    if is_update:
        reason_codes.append("update_signal")
    if is_preference:
        reason_codes.append("preference_signal")
    if is_noise:
        reason_codes.append("noise_signal")
    if is_future_observation:
        reason_codes.append("future_observation")

    urgency_score = _score_urgency(due_at, scheduled_at, now)
    salience_score = _score_salience(
        topics=topics,
        due_at=due_at,
        scheduled_at=scheduled_at,
        is_actionable=is_actionable,
        is_commitment=is_commitment,
        is_update=is_update,
        is_preference=is_preference,
    )

    return EventSignal(
        event_id=event.event_id,
        topics=topics,
        date_mentions=date_mentions,
        due_at=due_at,
        scheduled_at=scheduled_at,
        is_actionable=is_actionable,
        is_commitment=is_commitment,
        is_preference=is_preference,
        is_noise=is_noise,
        is_update=is_update,
        is_future_observation=is_future_observation,
        urgency_score=urgency_score,
        salience_score=salience_score,
        reason_codes=reason_codes,
    )


def _extract_topics(content_lower: str) -> set[str]:
    topics: set[str] = set()
    for topic, patterns in TOPIC_PATTERNS.items():
        if any(pattern in content_lower for pattern in patterns):
            topics.add(topic)

    if "proposal" in content_lower and ("nina" in content_lower or "northstar" in content_lower):
        topics.add("uie_proposal")

    if "procurement" in content_lower and "uie" in content_lower:
        topics.add("uie_proposal")

    if "mom" in content_lower and "report" in content_lower:
        topics.add("mom_cardiology")

    return topics


def _extract_date_mentions(event: EventRecord, content_lower: str) -> list[datetime]:
    mentions: list[datetime] = []
    month_pattern = "|".join(MONTHS)
    exact_date_pattern = re.compile(
        rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:\s+(\d{{1,2}}):(\d{{2}})\s*(ist)?)?",
        flags=re.IGNORECASE,
    )
    for match in exact_date_pattern.finditer(content_lower):
        month = MONTHS[match.group(1)[:3].lower()]
        day = int(match.group(2))
        hour = int(match.group(3)) if match.group(3) else 18
        minute = int(match.group(4)) if match.group(4) else 0
        mentions.append(ist_datetime(month, day, hour, minute))

    if "eod" in content_lower:
        mentions.append(end_of_day_ist(event.timestamp))

    if "tonight" in content_lower:
        local_event = to_ist(event.timestamp)
        mentions.append(
            datetime(
                local_event.year,
                local_event.month,
                local_event.day,
                22,
                0,
                tzinfo=local_event.tzinfo,
            ).astimezone(UTC)
        )

    weekday_mentions = _extract_weekday_mentions(event, content_lower)
    mentions.extend(weekday_mentions)
    return sorted(set(mentions))


def _extract_weekday_mentions(event: EventRecord, content_lower: str) -> list[datetime]:
    mentions: list[datetime] = []
    if "appointment" in content_lower and not re.search(r"\bapr\s+\d{1,2}\b", content_lower):
        return mentions

    event_local = to_ist(event.timestamp)
    for weekday_name, weekday_index in WEEKDAYS.items():
        if weekday_name not in content_lower:
            continue
        if re.search(rf"\b{weekday_name}\s+apr\s+\d{{1,2}}\b", content_lower):
            continue
        delta_days = (weekday_index - event_local.weekday()) % 7
        if delta_days == 0:
            due_date = event_local.date()
        else:
            due_date = (event_local + timedelta(days=delta_days)).date()
        hour = 10 if "morning" in content_lower else 18
        mentions.append(
            datetime(due_date.year, due_date.month, due_date.day, hour, 0, tzinfo=event_local.tzinfo)
            .astimezone(UTC)
        )
    return mentions


def _extract_due_at(
    event: EventRecord,
    content_lower: str,
    date_mentions: list[datetime],
) -> datetime | None:
    if not date_mentions:
        return None

    has_due_language = any(
        pattern in content_lower
        for pattern in (
            "due",
            "before",
            "by ",
            "promised",
            "portal locks",
            "requires owner confirmation",
            "if you cannot confirm",
            "at least one hour before",
            "please send",
            "need the",
            "needs",
            "owed",
            "owe",
        )
    )
    if has_due_language:
        if _prefers_latest_date(content_lower):
            return max(date_mentions)
        return min(date_mentions)

    if event.source == "reminder":
        return min(date_mentions)

    if event.source == "calendar" and "pending confirmation" in content_lower:
        return min(date_mentions)

    return None


def _extract_scheduled_at(
    event: EventRecord,
    content_lower: str,
    date_mentions: list[datetime],
) -> datetime | None:
    if not date_mentions:
        return None

    has_schedule_language = event.source == "calendar" or any(
        pattern in content_lower
        for pattern in (
            "scheduled",
            "review",
            "standup",
            "sync",
            "appointment",
            "meeting",
            "call",
            "working session",
            "focus block",
            "prep",
            "negotiation",
            "calibration",
        )
    )
    if not has_schedule_language:
        return None

    if event.source == "calendar" and "tonight" in content_lower:
        return max(date_mentions)

    if _prefers_latest_date(content_lower):
        return max(date_mentions)

    return min(date_mentions)


def _prefers_latest_date(content_lower: str) -> bool:
    return any(
        pattern in content_lower
        for pattern in (
            "moved from",
            "ignore my earlier",
            "now due",
            "not friday",
            "instead",
            "actually",
        )
    )


def _score_urgency(
    due_at: datetime | None,
    scheduled_at: datetime | None,
    now: datetime,
) -> float:
    urgency_score = 0.0
    today_start = start_of_day_ist(now)
    tomorrow_start = next_day_start_ist(now)
    two_days = now + timedelta(hours=48)

    for target in (due_at, scheduled_at):
        if not target:
            continue
        if target < now:
            urgency_score += 2.0
        elif today_start <= target < tomorrow_start:
            urgency_score += 1.8
        elif target <= two_days:
            urgency_score += 1.2
        else:
            urgency_score += 0.4

    return urgency_score


def _score_salience(
    topics: set[str],
    due_at: datetime | None,
    scheduled_at: datetime | None,
    is_actionable: bool,
    is_commitment: bool,
    is_update: bool,
    is_preference: bool,
) -> float:
    salience_score = 0.0
    if topics:
        salience_score += 0.8 + (0.2 * len(topics))
    if due_at:
        salience_score += 1.0
    if scheduled_at:
        salience_score += 0.6
    if is_actionable:
        salience_score += 0.7
    if is_commitment:
        salience_score += 0.8
    if is_update:
        salience_score += 0.9
    if is_preference:
        salience_score += 0.4
    return salience_score

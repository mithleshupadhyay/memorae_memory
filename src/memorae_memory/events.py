from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memorae_memory.schemas import EventRecord
from memorae_memory.time_utils import parse_utc


def load_events(dataset_path: str | Path) -> list[EventRecord]:
    path = Path(dataset_path)
    with path.open(encoding="utf-8") as dataset_file:
        raw_events: list[dict[str, Any]] = json.load(dataset_file)

    events: list[EventRecord] = []
    for event_id, raw_event in enumerate(raw_events):
        events.append(
            EventRecord(
                event_id=event_id,
                timestamp=parse_utc(raw_event["timestamp"]),
                source=str(raw_event["source"]).strip(),
                content=str(raw_event["content"]).strip(),
            )
        )

    return events

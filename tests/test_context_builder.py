from __future__ import annotations

import unittest
from datetime import UTC, datetime

from memorae_memory.retrieval.context_builder import build_context
from memorae_memory.schemas import CandidateEvent, EventRecord, EventSignal


def make_candidate(event_id: int, content: str, topic: str = "topic") -> CandidateEvent:
    event = EventRecord(
        event_id=event_id,
        timestamp=datetime(2026, 4, 13, 3, 0, tzinfo=UTC),
        source="slack",
        content=content,
    )
    signal = EventSignal(event_id=event_id, topics={topic})
    return CandidateEvent(
        event=event,
        signal=signal,
        score=10.0 - event_id,
        reasons=["test"],
    )


class ContextBuilderTest(unittest.TestCase):
    def test_dedupes_repeated_content_and_respects_event_limit(self) -> None:
        candidates = [
            make_candidate(0, "Same useful update."),
            make_candidate(1, "Same useful update."),
            make_candidate(2, "Different useful update."),
            make_candidate(3, "Another useful update."),
        ]

        response = build_context(candidates, max_events=2, max_tokens=50)

        self.assertEqual([item.event.event_id for item in response.context], [0, 2])
        self.assertEqual(response.ignored_summary["counts"]["duplicate_content"], 1)
        self.assertEqual(response.ignored_summary["counts"]["context_event_limit"], 1)

    def test_respects_token_budget(self) -> None:
        candidates = [
            make_candidate(0, "one two three four five"),
            make_candidate(1, "six seven eight nine ten"),
        ]

        response = build_context(candidates, max_events=5, max_tokens=6)

        self.assertEqual([item.event.event_id for item in response.context], [0])
        self.assertEqual(response.ignored_summary["counts"]["token_budget"], 1)


if __name__ == "__main__":
    unittest.main()

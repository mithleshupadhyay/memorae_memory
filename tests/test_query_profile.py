from __future__ import annotations

import unittest
from pathlib import Path

from memorae_memory.events import load_events
from memorae_memory.query import QueryProfiler
from memorae_memory.signals import extract_signals

DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "memorae_mock_events.json"


class QueryProfilerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.events = load_events(DATASET_PATH)
        cls.signals = extract_signals(cls.events)
        cls.profiler = QueryProfiler(events=cls.events, signals=cls.signals)

    def test_broad_risk_query_has_no_topic_constraint(self) -> None:
        profile = self.profiler.analyze("What commitments am I at risk of missing?")

        self.assertEqual(profile.selected_topics, set())

    def test_unseen_topic_query_infers_matching_cluster(self) -> None:
        profile = self.profiler.analyze("Summarize Southridge SOW status.")

        self.assertEqual(profile.selected_topics, {"southridge_sow"})
        self.assertGreater(profile.topic_scores["southridge_sow"], 1.0)


if __name__ == "__main__":
    unittest.main()

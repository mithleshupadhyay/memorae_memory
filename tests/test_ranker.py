from __future__ import annotations

import unittest
from pathlib import Path

from memorae_memory.events import load_events
from memorae_memory.query import QueryProfiler
from memorae_memory.retrieval.ranker import CandidateRanker, RankingConfig
from memorae_memory.signals import extract_signals
from memorae_memory.time_utils import SCENARIO_NOW

DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "memorae_mock_events.json"


class CandidateRankerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.events = load_events(DATASET_PATH)
        cls.signals = extract_signals(cls.events)
        cls.profiler = QueryProfiler(events=cls.events, signals=cls.signals)
        cls.ranker = CandidateRanker(events=cls.events, signals=cls.signals, now=SCENARIO_NOW)

    def test_topic_summary_does_not_pad_with_unrelated_clusters(self) -> None:
        profile = self.profiler.analyze("Summarize Southridge SOW status.")

        candidates = self.ranker.retrieve(profile=profile)

        self.assertGreater(len(candidates), 0)
        self.assertTrue(
            all("southridge_sow" in candidate.signal.topics for candidate in candidates)
        )

    def test_dental_risk_query_ranks_dental_context_first(self) -> None:
        profile = self.profiler.analyze("What is at risk for the dental slot?")

        candidates = self.ranker.retrieve(profile=profile, max_candidates=3)
        candidate_ids = [candidate.event.event_id for candidate in candidates]

        self.assertIn(94, candidate_ids)
        self.assertIn("dentist", candidates[0].signal.topics)

    def test_candidates_include_score_breakdown(self) -> None:
        profile = self.profiler.analyze("Summarize everything related to the UIE proposal.")

        candidates = self.ranker.retrieve(profile=profile, max_candidates=3)

        self.assertGreater(len(candidates), 0)
        self.assertIn("bm25", candidates[0].score_breakdown)
        self.assertIn("topic_match", candidates[0].score_breakdown)
        self.assertIn("summary_signal", candidates[0].score_breakdown)

    def test_ranker_rejects_missing_signals(self) -> None:
        incomplete_signals = {self.events[0].event_id: self.signals[self.events[0].event_id]}

        with self.assertRaisesRegex(ValueError, "missing signals"):
            CandidateRanker(
                events=self.events[:2],
                signals=incomplete_signals,
                now=SCENARIO_NOW,
            )

    def test_configurable_reason_limit_is_respected(self) -> None:
        ranker = CandidateRanker(
            events=self.events,
            signals=self.signals,
            now=SCENARIO_NOW,
            config=RankingConfig(max_reason_count=2),
        )
        profile = self.profiler.analyze("What should I focus on today?")

        candidates = ranker.retrieve(profile=profile, max_candidates=5)

        self.assertGreater(len(candidates), 0)
        self.assertLessEqual(len(candidates[0].reasons), 2)

    def test_invalid_ranker_limits_are_rejected(self) -> None:
        profile = self.profiler.analyze("What should I focus on today?")

        with self.assertRaisesRegex(ValueError, "max_candidates"):
            self.ranker.retrieve(profile=profile, max_candidates=0)

        with self.assertRaisesRegex(ValueError, "max_reason_count"):
            RankingConfig(max_reason_count=0)


if __name__ == "__main__":
    unittest.main()

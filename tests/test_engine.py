from __future__ import annotations

import unittest
from pathlib import Path

from memorae_memory.engine import MemoryEngine


DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "memorae_mock_events.json"


class MemoryEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = MemoryEngine.from_dataset(str(DATASET_PATH))

    def test_uie_summary_keeps_latest_updates_in_context(self) -> None:
        response = self.engine.answer("Summarize everything related to the UIE proposal.")
        selected_ids = {context_event.event.event_id for context_event in response.selected_context}

        self.assertIn(108, selected_ids)
        self.assertIn(110, selected_ids)
        self.assertIn(126, selected_ids)
        self.assertIn(152, selected_ids)
        self.assertIn(170, selected_ids)
        self.assertIn("Unified Intelligence Engine", response.answer)
        self.assertIn("$48.5k", response.answer)
        self.assertIn("Apr 13 15:00 IST", response.answer)
        self.assertNotIn("now due Friday Apr 10", response.answer)

    def test_today_focus_selects_calendar_review_not_generic_focus_blocks(self) -> None:
        response = self.engine.answer("What should I focus on today?")
        selected_ids = {context_event.event.event_id for context_event in response.selected_context}

        self.assertIn(108, selected_ids)
        self.assertIn(164, selected_ids)
        self.assertIn(170, selected_ids)
        self.assertIn(94, selected_ids)
        self.assertNotIn(75, selected_ids)
        self.assertNotIn(137, selected_ids)

    def test_risk_query_keeps_due_soon_personal_deadlines(self) -> None:
        response = self.engine.answer("What commitments am I at risk of missing?")
        selected_ids = {context_event.event.event_id for context_event in response.selected_context}

        self.assertIn(33, selected_ids)
        self.assertIn(77, selected_ids)
        self.assertIn(94, selected_ids)
        self.assertIn(164, selected_ids)

    def test_required_queries_return_inspectable_reasoning(self) -> None:
        for query in [
            "What should I focus on today?",
            "What commitments am I at risk of missing?",
            "What have I been procrastinating on?",
            "Summarize everything related to the UIE proposal.",
        ]:
            with self.subTest(query=query):
                response = self.engine.answer(query)
                payload = response.to_dict()

                self.assertTrue(payload["answer"])
                self.assertGreater(len(payload["selected_context"]), 0)
                self.assertIn("why_selected", payload["reasoning"])
                self.assertIn("why_ignored_or_downweighted", payload["reasoning"])
                self.assertIn("contradiction_and_recency_resolution", payload["reasoning"])


if __name__ == "__main__":
    unittest.main()

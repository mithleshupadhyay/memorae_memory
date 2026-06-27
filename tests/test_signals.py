from __future__ import annotations

import unittest
from pathlib import Path

from memorae_memory.events import load_events
from memorae_memory.signals import extract_signals
from memorae_memory.time_utils import parse_utc


DATASET_PATH = Path(__file__).resolve().parents[1] / "data" / "memorae_mock_events.json"


class SignalExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.events = load_events(DATASET_PATH)
        cls.signals = extract_signals(cls.events)

    def test_dataset_loads_all_events(self) -> None:
        self.assertEqual(len(self.events), 200)

    def test_uie_deadline_update_prefers_latest_date(self) -> None:
        signal = self.signals[108]

        self.assertEqual(signal.due_at, parse_utc("2026-04-13T09:30:00Z"))
        self.assertIn("uie_proposal", signal.topics)
        self.assertTrue(signal.is_update)

    def test_uie_calendar_move_prefers_new_review_time(self) -> None:
        signal = self.signals[110]

        self.assertEqual(signal.scheduled_at, parse_utc("2026-04-13T09:00:00Z"))
        self.assertTrue(signal.is_update)

    def test_calendar_appointment_keeps_exact_appointment_date(self) -> None:
        signal = self.signals[68]

        self.assertEqual(signal.scheduled_at, parse_utc("2026-04-14T03:30:00Z"))
        self.assertIn("mom_cardiology", signal.topics)

    def test_noisy_random_message_is_marked_noise(self) -> None:
        signal = self.signals[2]

        self.assertTrue(signal.is_noise)
        self.assertLess(signal.salience_score, 0)


if __name__ == "__main__":
    unittest.main()

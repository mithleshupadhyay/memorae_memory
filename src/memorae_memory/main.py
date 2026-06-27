from __future__ import annotations

import argparse
import json
from pathlib import Path

from memorae_memory.engine import DEFAULT_QUERIES, MemoryEngine
from memorae_memory.time_utils import SCENARIO_NOW, parse_utc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Memorae personal-memory assessment engine."
    )
    parser.add_argument(
        "--dataset",
        default="data/memorae_mock_events.json",
        help="Path to the raw event JSON file.",
    )
    parser.add_argument(
        "--query",
        action="append",
        help="Question to answer. Can be passed more than once. Defaults to the four required queries.",
    )
    parser.add_argument(
        "--now",
        default=SCENARIO_NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        help="Scenario time in UTC ISO format.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write JSON results.",
    )
    args = parser.parse_args()

    engine = MemoryEngine.from_dataset(args.dataset, now=parse_utc(args.now))
    queries = args.query or DEFAULT_QUERIES
    responses = [engine.answer(query).to_dict() for query in queries]
    payload = {
        "scenario_time": args.now,
        "dataset": str(Path(args.dataset)),
        "responses": responses,
    }

    rendered_payload = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered_payload + "\n", encoding="utf-8")

    print(rendered_payload)


if __name__ == "__main__":
    main()

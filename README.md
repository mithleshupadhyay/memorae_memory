# Memorae Memory Assessment

Inspectable personal-memory retrieval and answering engine for the Memorae AI Engineer assessment.

The implementation uses the raw `data/memorae_mock_events.json` file without modifying it or adding manual labels to it. Topics, deadlines, stale updates, urgency, and context clusters are derived at runtime.

## Setup

Requires Python 3.12+. No external runtime dependencies are required.

```bash
python --version
```

Run all required assessment queries:

```bash
PYTHONPATH=src python -m memorae_memory.main --dataset data/memorae_mock_events.json
```

Write inspectable JSON output:

```bash
PYTHONPATH=src python -m memorae_memory.main \
  --dataset data/memorae_mock_events.json \
  --output outputs/example_run.json
```

Run one query:

```bash
PYTHONPATH=src python -m memorae_memory.main \
  --dataset data/memorae_mock_events.json \
  --query "Summarize everything related to the UIE proposal."
```

Run tests:

```bash
PYTHONPATH=src python -m unittest
```

## What The Engine Does

For each query the engine performs four steps:

1. Source and signal selection: classifies query intent, extracts runtime signals, ranks candidate events with BM25 plus urgency/topic/update scoring.
2. Context construction: builds a bounded context with event caps, token estimates, topic caps, deduping, and ignored-context diagnostics.
3. Answer generation: returns a specific, time-aware answer grounded in the selected event stream.
4. Reasoning explanation: reports selected clusters, why records mattered, downranked/ignored evidence, stale update resolution, and uncertainty.

Default scenario time is fixed to:

```text
2026-04-13T03:00:00Z
```

The CLI supports `--now` if the reviewer wants to test another scenario timestamp.

## Files

- `src/memorae_memory/engine.py`: intent classification, retrieval orchestration, answer generation, reasoning.
- `src/memorae_memory/signals.py`: runtime signal extraction for topics, deadlines, updates, noise, preferences, and urgency.
- `src/memorae_memory/retrieval/bm25.py`: dependency-free BM25 index.
- `src/memorae_memory/retrieval/context_builder.py`: selective context construction.
- `docs/DESIGN.md`: design document.
- `docs/EVALUATION.md`: evaluation framework and optimization answer.
- `tests/`: regression tests.

## External APIs

None. The submission is deterministic and runnable offline.

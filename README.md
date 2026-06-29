# Memorae Memory Assessment

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Poetry](https://img.shields.io/badge/poetry-dependency%20management-blue.svg)](https://python-poetry.org/)
[![Offline](https://img.shields.io/badge/external%20APIs-none-green.svg)](#external-apis)

An inspectable personal-memory retrieval and answering engine for the Memorae AI Engineer assessment.

The system reads a raw personal event stream from `data/memorae_mock_events.json`, derives runtime memory signals, retrieves the right context for each question, and returns grounded answers with selected events and reasoning.

## Quick Start

### Prerequisites

- Python 3.12+
- Poetry

### Installation

```bash
# Install dependencies and the local package
poetry install

# Run all required assessment queries
poetry run memorae-memory --dataset data/memorae_mock_events.json

# Generate inspectable JSON output
poetry run memorae-memory \
  --dataset data/memorae_mock_events.json \
  --output outputs/example_run.json
```

### Makefile Shortcuts

```bash
# Run all required queries
make run

# Regenerate outputs/example_run.json
make example

# Run regression tests
make test

# Run lint checks
make lint
```

## Basic Usage

```bash
# Run with default scenario time
poetry run memorae-memory --dataset data/memorae_mock_events.json

# Run one query
poetry run memorae-memory \
  --dataset data/memorae_mock_events.json \
  --query "Summarize everything related to the UIE proposal."

# Override scenario time
poetry run memorae-memory \
  --dataset data/memorae_mock_events.json \
  --now 2026-04-13T03:00:00Z

# Save output to a file
poetry run memorae-memory \
  --dataset data/memorae_mock_events.json \
  --output outputs/example_run.json
```

## Required Queries

The engine answers the four required assessment queries:

| Query | Runtime intent |
| --- | --- |
| `What should I focus on today?` | Prioritize urgent work and personal commitments for Apr 13 IST. |
| `What commitments am I at risk of missing?` | Find overdue or near-deadline commitments. |
| `What have I been procrastinating on?` | Detect repeated nudges, stale asks, and slipping tasks. |
| `Summarize everything related to the UIE proposal.` | Infer the UIE topic cluster and summarize relevant updates, deadlines, dependencies, and preferences. |

Default scenario time:

```text
2026-04-13T03:00:00Z
```

## Features

- Runtime signal extraction from raw events without modifying the dataset.
- Topic clustering for UIE proposal, hiring rubric, Southridge SOW, personal admin, and other workstreams.
- Query-time topic inference from overlap between the user's words and event-derived cluster terms.
- IST-aware deadline and calendar parsing.
- Hybrid retrieval using dependency-free BM25 plus urgency, commitment, update, recency, repeated-ask, dependency, and noise scoring.
- Selective context construction with event limits, token estimates, deduping, and topic caps.
- Stale fact and contradiction handling.
- Inspectable JSON output with selected context, reasoning, diagnostics, and ignored-context summaries.
- Offline deterministic execution with no external API dependency.
- Regression tests for retrieval, signal extraction, context building, required query behavior, and unseen topic queries.

## Architecture

```
+------------------+    +------------------+    +------------------+
| Raw Event Stream | -> | Signal Extractor | -> | Hybrid Retriever |
| JSON dataset     |    | topics/dates     |    | BM25 + signals   |
+------------------+    +------------------+    +------------------+
                                                         |
                                                         v
+------------------+    +------------------+    +------------------+
| JSON Response    | <- | Answer Generator | <- | Context Builder  |
| answer+reasoning |    | extractive synth |    | bounded context  |
+------------------+    +------------------+    +------------------+
```

## Project Structure

```
memorae/
|-- data/
|   `-- memorae_mock_events.json
|-- docs/
|   |-- DESIGN.md
|   |-- EVALUATION.md
|   `-- WALKTHROUGH.md
|-- outputs/
|   `-- example_run.json
|-- src/
|   `-- memorae_memory/
|       |-- engine.py
|       |-- events.py
|       |-- main.py
|       |-- schemas/
|       |   |-- __init__.py
|       |   `-- models.py
|       |-- signals.py
|       |-- time_utils.py
|       `-- retrieval/
|           |-- bm25.py
|           `-- context_builder.py
|-- tests/
|   |-- test_context_builder.py
|   |-- test_engine.py
|   `-- test_signals.py
|-- Makefile
|-- poetry.lock
|-- poetry.toml
`-- pyproject.toml
```

## How It Works

For each query, the engine performs the required four-step flow:

1. Source and signal selection
   - Classifies broad query intent such as focus, risk, procrastination, summary, or generic.
   - Infers topic constraints from query/event cluster overlap when the query names a workstream.
   - Extracts runtime signals from the raw stream.
   - Scores candidate events using filtered lexical relevance, urgency, topics, commitments, updates, recency, repeated asks, dependencies, and noise penalties.

2. Context construction
   - Builds a bounded context instead of passing every event.
   - Dedupes repeated content.
   - Applies token estimates and topic caps.
   - Reports what was ignored or downweighted.

3. Answer generation
   - Produces an extractive, time-aware answer from selected evidence.
   - Grounds the answer in selected event IDs.
   - States uncertainty when completion evidence is missing.

4. Reasoning explanation
   - Lists selected clusters.
   - Explains why events mattered.
   - Shows ignored/downweighted categories.
   - Explains stale fact and contradiction resolution.

## Contradiction Handling

The engine resolves stale facts using newer explicit updates:

| Topic | Resolution |
| --- | --- |
| UIE deadline | Apr 10 is stale; latest deadline is Apr 13 15:00 IST. |
| UIE review | Apr 10 review is stale; latest review is Apr 13 14:30 IST. |
| UIE procurement | Old `$42k` estimate is stale; latest estimate is `$48.5k`. |
| Ravi/data-room | Access is waiting on external-safe diagrams, not procurement. |
| Southridge SOW | Clause 8 is no longer blocked after legal approval. |
| UIE work block | Apr 12 work block was cancelled; Apr 13 appendix block remains relevant. |

## Output Format

The CLI returns JSON with one response per query:

```json
{
  "query": "What should I focus on today?",
  "intent": "today_focus",
  "answer": "...",
  "selected_context": [
    {
      "event_id": 108,
      "timestamp": "2026-04-09T07:55:00Z",
      "source": "slack",
      "content": "...",
      "why_selected": ["commitment/deadline language", "update or correction"]
    }
  ],
  "reasoning": {
    "query_profile": {
      "intent": "today_focus",
      "query_terms": ["focus", "today"],
      "inferred_topics": []
    },
    "why_selected": "...",
    "why_ignored_or_downweighted": "...",
    "contradiction_and_recency_resolution": ["..."],
    "uncertainty": "..."
  }
}
```

See the generated example at `outputs/example_run.json`.

## Documentation

- `docs/DESIGN.md` - retrieval architecture, memory architecture, context strategy, contradiction handling, failure modes, scaling plan, and optimization answer.
- `docs/EVALUATION.md` - offline evals, online evals, regression tests, metrics, and human review rubric.
- `docs/WALKTHROUGH.md` - plain-English end-to-end explanation of what the project is and how it works.
- `outputs/example_run.json` - generated output for the required queries.

## Development

### Install

```bash
poetry install
```

### Testing

```bash
# Run all regression tests
poetry run python -m unittest

# Or use Makefile
make test
```

### Linting

```bash
poetry run ruff check src tests

# Or use Makefile
make lint
```

### Regenerate Example Output

```bash
make example
```

## External APIs

None.

The implementation is deterministic and runnable offline. It does not call OpenAI, embedding services, rerankers, vector databases, or any network service.

## Submission Checklist

Include these files and directories:

- `data/memorae_mock_events.json`
- `src/memorae_memory/`
- `tests/`
- `docs/`
- `outputs/example_run.json`
- `README.md`
- `pyproject.toml`
- `poetry.lock`
- `poetry.toml`
- `Makefile`

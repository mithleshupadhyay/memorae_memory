# Design Document

## Goal

The engine answers ambiguous personal-memory questions from a raw stream of 200 events. It assumes the current time is `2026-04-13T03:00:00Z` and must reason about what is urgent, overdue, stale, future-facing, and important without adding labels to the dataset.

The implementation is intentionally dependency-free. In production I would replace some deterministic pieces with embedding retrieval, reranking, and LLM synthesis, but this assessment version keeps every decision inspectable and runnable offline.

## Retrieval Architecture

Retrieval is hybrid:

1. Query intent classification maps the user question to one of four main intents: today focus, risk of missing commitments, procrastination, or UIE proposal summary.
2. Runtime signal extraction derives topics, dates, deadlines, scheduled events, action language, commitment language, update/correction language, preferences, noisy records, and future-observation flags.
3. A small BM25 index provides lexical recall over raw content plus derived runtime terms such as topic names and signal codes.
4. Intent-specific scoring combines BM25 with business signals. For example, the today query boosts Apr 13 IST calendar anchors and overdue commitments; the UIE query boosts corrections such as the Apr 10 to Apr 13 deadline update and the $48.5k procurement correction.
5. Context construction caps selected records by event count, token estimate, duplicate content, and topic density.

This gives the system more product judgment than pure keyword search. A random Slack message can match words, but it is downweighted if it is noisy, unrelated, or low urgency.

## Memory Architecture

The raw event stream is treated as the source of truth. The system derives temporary memory structures at runtime:

- Event memory: normalized timestamp, source, content, and stable event ID.
- Signal memory: extracted topics, due dates, scheduled dates, urgency, actionability, and stale-update markers.
- Cluster memory: topic groups such as `uie_proposal`, `hiring_rubric`, `southridge_sow`, `mom_cardiology`, `admin_export`, and `incident_doc`.
- Preference memory: durable user or stakeholder preferences such as Nina preferring risk-first decision memos and Aarav preferring agenda-first meetings.
- Update memory: corrections and supersessions such as moved deadlines, approved blockers, old estimates, and cancelled blocks.

No derived labels are written back into the dataset. In a larger system, these structures would be persisted in a feature/index store with provenance back to the raw event IDs.

## Context Construction Strategy

The production prompt budget in the assignment is 100k tokens, but the system should not fill it. The implementation estimates tokens by word count and keeps only the highest-value records for each query.

For this dataset:

- Today and risk queries select roughly 18-20 records because they span several active workstreams.
- The UIE proposal summary selects more records because the query asks for "everything related" to a single dense cluster.
- Context builder dedupes repeated content and enforces a topic cap to avoid one noisy cluster crowding out other commitments.
- The output includes ignored-context diagnostics so the reviewer can see whether records were excluded because of event limits, token budget, duplicate text, or topic caps.

At 10k messages, 1k notes, and 500 reminders, the same shape scales by moving retrieval into stages:

1. Metadata prefilter by time window, source, known entity/topic, and actionability.
2. Lexical and vector retrieval over chunks/events.
3. Lightweight rerank with urgency, recency, source reliability, and update status.
4. Cluster summarization for repeated or historical signals.
5. Final context assembly with per-cluster budgets and citation-backed snippets.

## Contradiction And Recency Handling

The engine treats newer explicit updates as stronger than older facts:

- UIE deadline: the original Friday Apr 10 deadline is superseded by the Apr 9 update moving it to Monday Apr 13 15:00 IST.
- UIE review: the Apr 10 review is superseded by the Apr 13 14:30 IST calendar update.
- Procurement: the old $42k estimate is superseded by the updated $48.5k year-one estimate.
- Ravi/data-room: the latest signal says access is waiting on external-safe diagrams, not procurement.
- Southridge: clause 8 is no longer treated as a blocker after the Apr 11 approval.
- UIE work block: the Apr 12 block cancellation is respected, while the Apr 13 appendix block remains relevant.

The reasoning block returns these resolutions for inspectability.

## Answer Generation

This assessment version uses deterministic synthesis templates by intent. The templates are grounded by the selected context and explain uncertainty when completion evidence is absent.

For example, "Summarize everything related to the UIE proposal" includes:

- Latest due date and review time.
- Nina's requested format.
- Required proposal and appendix content.
- Procurement correction.
- Retry-budget dependency.
- Ravi/data-room dependency.
- Post-delivery FAQ follow-up.
- Uncertainty that no final sent-confirmation exists.

In production, a small model could generate the final prose from the constructed context, but the same selected context and reasoning metadata should remain visible.

## Failure Modes

Known risks:

- Date ambiguity: messages like "Tuesday appointment" without a date may require later calendar events to disambiguate.
- Completion uncertainty: the stream often contains asks but not completion events, so unresolved status is inferred.
- Future timestamps: the dataset contains events after the scenario time. The engine treats future calendar/reminder events as known schedule, while non-calendar future messages are downweighted unless they contain a near-term deadline or explicit update.
- Topic ambiguity: "redlines" can refer to multiple workstreams unless another term anchors it.
- Template brittleness: deterministic answer templates are reliable here but less flexible than model-based synthesis for unseen query types.
- Missing private context: the system cannot know whether the user completed something outside the observed stream.

## Scaling Plan

For a larger personal-memory dataset:

- Store raw events in an append-only event log.
- Extract runtime and persisted signals with provenance, confidence, and timestamps.
- Maintain topic/entity indexes using a hybrid of lexical search, embeddings, and structured filters.
- Build rolling summaries per topic and per commitment, but always keep citations to raw records.
- Recompute priority queues for upcoming deadlines and stale commitments.
- Use a small reranker for top 100-200 candidates, then build final context with cluster budgets.
- Keep user-specific preference memory separate from task memory, with decay and confirmation rules.

## Optimization Question

If latency must be under 2 seconds and cost must drop by 80%, I would:

- Precompute event signals, topic clusters, due dates, and rolling summaries at ingestion time.
- Use metadata filters and BM25 first, then embeddings only for ambiguous or low-recall queries.
- Cache query profiles and cluster summaries for common questions like "today" and "what am I missing?"
- Route simple deadline/status queries to deterministic code or a small model.
- Use a larger model only for final synthesis when the selected context is ambiguous or conflicting.
- Maintain memory tiers: hot commitments and today's calendar in a fast store, warm topic summaries in a document index, cold raw history in cheap storage.
- Keep answer quality by preserving citations, contradiction rules, and confidence/uncertainty fields even when using cheaper retrieval.

Tradeoff: latency and cost improve, but rare or highly nuanced queries may lose recall if precomputed summaries are stale or if embedding retrieval is skipped too aggressively.

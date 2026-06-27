# Evaluation Framework

## What Good Means

For subjective questions like "What should I focus on today?", a good answer is:

- Time-aware: uses `2026-04-13T03:00:00Z` and IST deadlines correctly.
- Prioritized: puts urgent and important items first, not merely recent messages.
- Grounded: every claim can be traced to selected events.
- Update-aware: stale facts are superseded by newer corrections.
- Selective: avoids dumping newsletters, receipts, random Slack chatter, and unrelated saved links.
- Useful: gives concrete next actions and names dependencies.
- Honest: states uncertainty when completion evidence is missing.

## Offline Evals

Build a small gold set of query expectations without modifying the dataset. The gold set should contain required facts, stale facts to avoid, and acceptable uncertainty.

Example test cases:

- UIE proposal summary must include Apr 13 15:00 IST due date, Apr 13 14:30 IST Nina review, Unified Intelligence Engine external naming, $48.5k estimate, data retention/SOC2/procurement appendix needs, retry-budget dependency, and Ravi/external-safe diagram dependency.
- UIE summary must not treat Apr 10 as the current due date or $42k as the current estimate.
- Today focus must include UIE proposal/appendix and hiring rubric before lower-signal personal tasks.
- Risk query must include hiring rubric overdue status, Southridge redlines, Mom cardiology summary, and due-soon personal admin.
- Procrastination query must include repeated nudges or "slips again" patterns such as redlines, admin export screenshots, school upload, insurance, incident doc, and UIE diagrams.

Metrics:

- Required-fact recall: percentage of expected facts present.
- Stale-fact precision: percentage of superseded facts avoided or explicitly marked stale.
- Citation coverage: percentage of answer claims supported by selected context.
- Noise rejection: percentage of selected context that is not random chatter, receipts, OTPs, or newsletters.
- Priority agreement: rank correlation between expected top priorities and answer order.
- Token efficiency: selected-context tokens divided by available budget.

## Online Evals

Online evaluation should measure whether the memory assistant helps the user act.

Signals:

- User accepts, edits, or dismisses suggested priorities.
- User clicks/open selected source events.
- User marks a commitment as done, snoozed, irrelevant, or wrong.
- Time to complete overdue tasks after assistant recommendation.
- Frequency of "why did you show this?" or "you missed X" feedback.
- Explicit trust rating on answers with conflicting evidence.

Guardrail metrics:

- False urgent rate: low-priority item shown as urgent.
- Missed urgent rate: urgent item not shown.
- Stale update rate: answer uses an old fact after a newer correction exists.
- Context bloat rate: answer uses more context than needed for comparable quality.

## Regression Tests

Regression tests should run on every change to signal extraction, retrieval, context building, or answer generation.

Current implemented tests should verify:

- UIE Apr 10 deadline is superseded by Apr 13.
- UIE review moved from Apr 10 to Apr 13.
- $48.5k estimate appears in UIE context.
- Random/noisy records are downweighted.
- Context builder dedupes repeated content and respects event/token caps.
- Required query outputs contain the expected high-level facts.

Additional regression cases to add with more time:

- Completion event added after an ask should remove it from overdue answers.
- Contradictory "not doing this anymore" update should cancel a commitment.
- Future non-calendar messages should not dominate today answers.
- Calendar events should remain usable as future schedule.
- Same topic with multiple stale reminders should collapse into one cluster summary.

## Human Review Rubric

For each answer, a human evaluator can score 1-5 on:

- Relevance: selected records actually answer the query.
- Specificity: answer names people, deadlines, dependencies, and next actions.
- Temporal correctness: deadlines and moved updates are handled correctly.
- Context discipline: answer does not include noisy or unrelated details.
- Uncertainty handling: ambiguous completion status is stated clearly.

An answer is production-acceptable if it averages at least 4, has no critical stale-fact errors, and includes citations for all high-impact claims.

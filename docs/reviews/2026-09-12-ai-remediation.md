# AI resilience remediation — 2026-09-12

This implementation addresses AR01–AR13 from the
[AI resilience review](2026-09-12-ai-resilience-review.md). Changes are committed on
`dev` as Patrik `<patrik@local>`. Provider tests use controlled responses and
disposable PostgreSQL/Redis services; no production credentials or paid provider
calls are needed.

| Finding | Implemented behavior | Regression evidence |
| --- | --- | --- |
| AR01 | Legacy and named connection tests require the caller's authorization context and recheck it before sending. | Actual API tests revoke the caller's token at both pre-I/O checkpoints and verify zero transmissions. |
| AR02 | Worker delivery ownership fences deferral, completion, fanout, provider reservation, cached provenance refresh and direct backfill recovery writes. Missing historical logical IDs cannot create replacement billable operations. | PostgreSQL tests resume old workers after replacement ownership and verify that current execution state and receipts remain intact. |
| AR03 | Governed and legacy cancellation share one service. Parent intent commits before child work; each child transaction releases its child/parent locks before the next child. | Concurrent child completion and cancellation through the active API service complete without deadlock. |
| AR04 | Queued cancellation reaches a durable terminal state without relying on Celery inspection or delivery. Maintenance propagates previously accepted intent in bounded batches. | Unknown inspection, legacy queued cancellation and policy changes between child transactions are covered. |
| AR05 | Feature output must contain storage-safe Unicode and bounded, finite JSON before successful receipt settlement. Optional model/diagnostic metadata is sanitized independently. Saved stages use the same storage checks. | NUL/surrogate, oversized title, nesting, node-count, byte-size, nonfinite-number and poisoned-metadata cases preserve known usage and received-response outcomes. |
| AR06 | Successful enrichment retains separate provenance. Briefs and report plans fall back to current primary evidence when freshness cannot be verified; stale relevance is omitted. Saved report evidence has an explicit contract version. | Real failed refresh, source changes, Unicode tags, migration history, cache ownership, report planning/storage and legacy report replay are covered. |
| AR07 | Numeric factual paragraphs, list bodies and table data rows require citations. | A shared Markdown corpus includes dates, counts, percentages, IP addresses and decorative nonclaims. |
| AR08 | Brief selection projects bounded text only for selected sources. Audit-only rows load bounded metadata. | A 40-source fixture materializes 4,500 summary characters; audit-only rows contain no summary bodies. |
| AR09 | Reprocessing progress uses targeted membership writes, bounded repair and SQL aggregation. | The same eight-statement recomputation ceiling applies to 100 and 500 children; repeated bounded repairs converge. |
| AR10 | Child history uses 50-row pages and retains the last accepted page on transient errors, with retry/reset controls and explicit scope. | Real QueryClient tests navigate all 1,000 children, page failures, parent changes, access loss and pruned history. |
| AR11 | Queue completion clears the scope only if it matches the submitted draft, including picker text. | Asynchronous edits to dates, limits, feed/item selection and picker search survive completion. |
| AR12 | Both connection diagnostics use 128 completion tokens, no retries and a request deadline of at most 30 seconds; the browser allows 45 seconds. | Legacy/named API parity tests cover success, truncation and upstream failure. |
| AR13 | Literal GFM autolinks cannot absorb accepted source citations. Explicit links and code retain their meaning. | A shared 25-case backend/web/HTML/PDF corpus and Chromium, Firefox and WebKit keyboard workflows verify visible source anchors. |

## Upgrade behavior

Migration `0100_ai_evidence_provenance` adds successful-result provenance and brief
evidence warnings. It deliberately leaves historical provenance unknown and does
not enqueue regeneration. Existing enrichment and completed report history remain
readable.

Deploy the API and AI workers together, draining or stopping older worker binaries
before relying on the new continuation fences. These checks protect executions
running the new code; an older binary does not acquire the new guards retroactively.

Report snapshots created before the new evidence contract cannot establish the
origin of their flat, frozen evidence strings. Generating or retrying one stops
before provider calls or saved-stage replay and directs the operator to create a
new report from current sources. Retrying the old report does not rewrite its
evidence or erase prior provider receipts. This also applies to an old snapshot
that happened to contain only primary text: the old format cannot prove that fact.

Canceling queued work prevents future execution but does not assert that an older
provider attempt was never sent. Existing ambiguous receipts still require their
normal reconciliation process. Cancellation accepted before a later access change
remains durable; subsequent HTTP transactions recheck current access and system
maintenance finishes the previously accepted intent.

## Validation

The full backend run completed with 3,078 passes and two skips. Its only failure
was a legacy compatibility assertion still expecting 5,000 diagnostic tokens;
the intended shared budget is now 128. The corrected three-test compatibility
module passed in a fresh run. The final covered rerun passed all four tests,
including the retained actual-worker regression for legacy report rejection.

Branch-inclusive backend coverage is 85.73%; reporting coverage is 86.36%, and all
critical-module floors pass. The frontend suite passes 1,066 tests across 122
files, with full lint, production build and production-bundle smoke checks. Nine
relevant provider and report workflows pass across Chromium, Firefox and WebKit,
including keyboard navigation and automated accessibility checks. The populated
PostgreSQL upgrade, full downgrade/re-upgrade and schema comparison pass, as do
the source-size, Ruff, generated API contract and whitespace checks. The frontend
dependency audit reports no vulnerabilities.

The isolated capacity smoke at `ee6542b` completed ingestion, AI diagnostics,
exports and governance activity with no budget violations. Its consumer recovery
took 7.38 seconds and the sampled test process peaked at 290.08 MiB RSS.
This short synthetic run establishes a regression check; it does not qualify
sustained throughput or real model behavior on deployment hardware.

## Remaining boundaries

Citation coverage and exact quotations establish structural provenance, not
semantic truth. Model qualification, sustained capacity qualification and
assistive-technology testing with actual screen readers remain separate work.
Future AI workers must enter the shared execution scope and use the common
transition helpers; future evidence formats must retain explicit origin/version
information. Automatic provider fallback must continue to respect configured
destinations and unresolved paid attempts.

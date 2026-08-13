# ADR 0002: Bounded AI Report Generation

- Status: Accepted
- Date: 2026-08-13

## Context

ThreatLens supports OpenAI-compatible hosted and local providers. Report generation must handle many long articles without assuming a tokenizer, a large context window, or high parallel capacity. Reports also need reproducible evidence, citation provenance, schedule idempotency, observable failure states, and connector-independent delivery.

Sending all matching content in one call would make memory, context overflow, latency, and cost unbounded. Generating directly from mutable articles, templates, or organization context would also make retries non-reproducible.

## Decision

ThreatLens uses a staged, bounded pipeline inside the existing backend and Celery deployment:

- Freeze selected source metadata, IOCs, URLs, and bounded evidence excerpts before queueing.
- Freeze company context and global instructions with the report.
- Estimate tokens conservatively without requiring a provider-specific tokenizer.
- Reserve output, framing overhead, and a safety percentage from every model context.
- Plan against the exact serialized provider messages so JSON escaping and envelope fields cannot invalidate preview estimates.
- Compact optional prompt context by priority while preserving the report objective and global instructions.
- Partition evidence into bounded synthesis batches, then generate individual sections from representative compact findings.
- Re-plan immutable source snapshots at execution so retries can degrade safely after switching to a smaller model.
- Generate deterministic scope, observable, and source sections without an AI call.
- Generate the executive summary after the evidence sections.
- Enforce source, per-source token, model-call, and worker-concurrency limits.
- Validate citations against the frozen source identifiers.
- Render downloadable artifacts from persisted sections.
- Emit `report_ready` through the transactional integration outbox.
- Run AI work on an isolated queue with one worker process by default.

Reporting remains a logical subsystem in the monolith rather than a new service. The dedicated worker is a deployment boundary, not a separate data owner.

## Consequences

Positive:

- Small local models receive predictable inputs and no single report can consume unbounded calls.
- Accepted reports retain the same source, report prompt, company context, and global-instruction snapshots across later article and template changes.
- Current provider and guardrail settings are revalidated at execution; bounded working prompts may tighten excerpts, compact optional context, or omit lower-ranked evidence instead of overrunning a newly selected smaller model.
- Queue and provider failures are visible and retryable.
- Delivery uses the same retry, circuit-breaker, dead-letter, and metrics infrastructure as other integrations.

Tradeoffs:

- Token counts are estimates and may over-reserve context.
- Hierarchical synthesis and adaptive compaction can lose lower-ranked detail; coverage and omission warnings make this visible.
- A report can take several serial model calls and the default single AI worker favors stability over throughput.
- A sufficiently restrictive provider or guardrail change can still make a queued report impossible when required framing and one evidence unit cannot fit; the saved report remains retryable after configuration is corrected.
- Provider-native tokenizer integration remains a future optional optimization, not a correctness dependency.

## Rejected Alternatives

- One prompt containing all articles: rejected because context and memory use are unbounded.
- Silent source truncation with no preview: rejected because operators need coverage visibility.
- Generate artifacts on demand with AI: rejected because output would not be reproducible.
- Connector-specific report tasks: rejected because it would bypass the generic transactional delivery engine.
- A separate reporting microservice: deferred because current scale and ownership do not justify another stateful deployment.

# Reporting

The Reporting workspace turns a filtered set of stored articles into a durable, sourced intelligence report. Reporting is available when server AI is enabled, an AI provider is configured, and **Intelligence report generation** is enabled in **Settings -> AI**.

## Access

- Route: `/reporting`
- `viewer`: read the report library, report details, source snapshots, and artifacts
- `analyst`: viewer access plus preview and manual generation, private templates, and owner-only retry/delete
- `admin`: all reporting actions, shared templates, and schedules
- Personal API tokens use `read:reports` and `write:reports`.

Generated reports are shared records. Reporting filters therefore cannot use private per-user read or starred state. Article text, notes, and mutable article state are not copied into the report response. The report stores the bounded evidence excerpts, metadata, tags, IOCs, and source URLs actually used during generation.

## Builder

The builder supports:

- full-text query, date window, feed, tag, classification, AI relevance, score, and extracted-text filters
- built-in, private, and shared templates
- audience, objective, tone, detail level, focus topics, exclusions, company context, and custom instructions
- enabled and ordered report sections
- per-source inclusion/exclusion from the live preview
- optional delivery through matching SMTP and webhook integrations
- link-only, summary, or bounded full-report delivery content

The live preview reports matching and selected source counts, total source tokens, the exact estimated peak input for one serialized provider call, batch count, model-call count, coverage, and omission warnings. Generation remains blocked while the preview is stale, invalid, empty, unavailable, or over a configured guardrail.

## Local-Model Guardrails

Reporting does not place the full corpus into one prompt. It:

1. conservatively estimates tokens using character, word, and denser long-fragment bounds for URLs, hashes, and observables
2. reserves output, protocol overhead, and a configurable safety margin
3. measures the actual serialized provider message, including JSON escaping and prompt framing
4. truncates each source to a configured token cap and tightens it further when a small context requires it
5. ranks and freezes at most the configured source limit
6. partitions evidence into context-safe batches without exceeding the model-call ceiling
7. synthesizes bounded findings from each batch
8. writes report sections from a representative, context-bounded finding set and generates the executive summary last
9. enforces a hard model-call ceiling
10. retries truncated structured output only within the exact unused context headroom for that call
11. rejects unknown citations and renders scope, source, and IOC sections deterministically

Configure these limits in **Settings -> AI -> Report Context Guardrails**. Set **Model Context Window** to the actual context supported by the loaded model and runtime, not the model family maximum. Conservative starting points are:

| Model context | Output reserve | Safety margin | Source cap |
| --- | ---: | ---: | ---: |
| 2K | 256 | 5-10% | 200-300 |
| 4K | 512 | 10-15% | 300-500 |
| 8K | 800-1,200 | 15-20% | 500-700 |

Keep AI worker concurrency at `1` for memory-constrained local inference. These are admission-control settings, not quality guarantees; very small models may still struggle to return valid structured JSON or follow citation instructions. When a provider reports output truncation, ThreatLens can increase the completion allowance on a bounded retry, but only into the exact context headroom left by that serialized prompt and never beyond the configured model completion cap.

The exact company context and global instructions are frozen when a report is queued, so later edits do not change the durable snapshot. Before each provider call, ThreatLens builds a bounded working projection from that snapshot. It preserves the objective and global instructions first, then fits custom instructions, topic lists, structured company fields, and profile text into the remaining prompt allowance. Compaction is recorded in report warnings.

The worker revalidates the current provider, model, context limits, and model-call ceiling at execution and retry time. If a queued report was planned for a larger model, execution tightens excerpts and omits only lower-ranked sources until the current limits fit, then records the changed coverage. It fails before a provider call only when the required protocol, objective, enabled AI sections, and one evidence unit cannot fit at all. Provider usage, exact planning telemetry, stages, model-call counts, and failures appear in AI task history and worker logs.

## Templates And Schedules

Built-in templates are immutable and can be cloned. Analysts can maintain private templates; only administrators can create or update shared templates.

Administrators can schedule weekly or monthly reports with:

- an IANA time zone and local execution time
- previous complete week, previous complete month, or rolling-day windows
- latest-only, skip, or bounded catch-up behavior (maximum four runs)
- optional schedule-specific instructions
- empty-period handling
- optional integration delivery and content mode

Calendar windows are calculated in the configured time zone, including daylight-saving transitions. Generation keys make scheduled periods idempotent.

## Artifacts And Delivery

Ready reports can be downloaded as Markdown, standalone HTML, or PDF. Artifacts are rendered from the persisted report snapshot rather than regenerated by AI.

When delivery is requested, the ready-report transaction writes one idempotent `report_ready` integration event. Existing SMTP and webhook hooks can subscribe to this event and retain generic delivery attempts, retries, circuit state, dead-letter replay, and metrics. Set `PUBLIC_APP_URL` so email and webhook templates receive an absolute `{{ brief.url }}` link.

## Failure Recovery

- Report creation and retry accept `Idempotency-Key`; an exact replay returns the original report while a conflicting payload is rejected.
- The web client keeps unresolved mutation keys in shared local browser storage and coordinates simultaneous tabs before dispatch. It migrates the previous session-storage formats in place and retains unresolved keys until a definitive response or authentication reset; when shared storage is blocked, the mutation is stopped before any request is sent.
- Report and AI task state commit before queue publication. A broker exception has an unknown outcome, so durable queued work is retried with the same task identity and capped exponential backoff instead of being marked failed. Once publication is confirmed, ThreatLens trusts the persistent broker and does not emit periodic duplicate messages while a task waits for an AI worker.
- Worker redelivery cannot make a second provider call while the original renewable generation lease is still owned. A superseded worker cannot persist sections or terminal state after ownership moves.
- Invalid template or context-budget configuration failures use capped retries and then quarantine the schedule. Transient planning failures use capped exponential backoff; after exhaustion, ThreatLens records the failure and advances to the next occurrence so one schedule cannot starve healthy schedules.
- Canceling a report from **Settings -> AI -> Activity** settles both records; generation also checks for cancellation between model calls.
- Lost report workers are reconciled into a terminal failure instead of leaving the report indefinitely queued or running.
- Provider and context errors retain actionable messages; unexpected exception details stay in worker logs while the UI receives a sanitized recovery message.
- Adaptive context decisions log usable input, fixed prompt size, peak serialized input, batch count, selected sources, omitted sources, and whether optional context was compacted.
- Failed or skipped reports can be retried by their owner or an administrator from the immutable source snapshot.
- Queued and running reports cannot be deleted.
- Scheduled empty periods are retained as skipped report records when **Skip periods with no sources** is enabled.

Operators can tune durable dispatch, schedule retry, generation leases, and rolling-upgrade grace with the `REPORT_*` and `CELERY_VISIBILITY_TIMEOUT_SECONDS` settings documented in the configuration reference. Keep the broker visibility timeout longer than the maximum expected report run to reduce duplicate queue load. If a run exceeds it, redelivery reuses the stable task ID and waits behind the renewable generation fence instead of repeating owned provider work. Ownership waits remain unbounded because another valid worker can still finish, while startup, ownership-verification, and settlement faults use a separate bounded exponential retry budget and become a durable task/report error when exhausted. During an upgrade, an unfenced `running` report from an older worker receives a 24-hour compatibility lease by default so the new worker cannot duplicate provider calls. Queued work published by an older binary is atomically superseded with a new task-run identity before it enters `ai-reports-v2`; a delayed message on `ai` then sees its original run as terminal and exits.

Do not roll the AI worker back while `ai-reports-v2` contains work. Stop report-producing API and maintenance processes, let the current worker drain that queue, and only then replace the worker binary. The documented local worker command consumes both `ai` and `ai-reports-v2`.

Migration `0050_report_idempotency_compat` keeps both idempotency columns during a normal rolling downgrade to `0049`. A deployment that accepted report requests on the unreleased rename-based draft must not roll back below `0047`: hash-only keys cannot be converted back into their original raw values. Released upgrade paths are unaffected.

Migration `0053_report_operation_receipts` is additive and preserves its receipt data on downgrade, so the previous backend can run while the table remains present and a later re-upgrade retains accepted keys. In a rolling deployment, migrate first, replace all API replicas, and then publish the matching web bundle: older API replicas do not understand idempotency headers for template, clone, or schedule creation and therefore cannot provide retry deduplication for those new UI requests.

Use `docker compose logs -f worker-ai` for generation diagnostics and the report detail plus **Settings -> AI -> Activity** for persisted stage/provider history.

## API

- `GET /reports/capabilities`
- `POST /reports/preview`
- `GET|POST /reports/templates`
- `PUT|DELETE /reports/templates/{template_id}`
- `POST /reports/templates/{template_id}/clone`
- `GET|POST /reports`
- `GET|DELETE /reports/{report_id}`
- `POST /reports/{report_id}/retry`
- `GET /reports/{report_id}/download?format=markdown|html|pdf`
- `GET|POST /reports/schedules`
- `PUT|DELETE /reports/schedules/{schedule_id}`
- `POST /reports/schedules/{schedule_id}/run`

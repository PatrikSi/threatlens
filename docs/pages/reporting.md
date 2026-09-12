# Reporting

The Reporting workspace turns a filtered set of stored articles into a durable, sourced intelligence report. Reporting is available when server AI is enabled, an AI provider is configured, and **Intelligence report generation** is enabled in **Settings -> AI**.

## Access

- Route: `/reporting`
- `viewer`: read the report library, report details, source snapshots, and artifacts
- `analyst`: viewer access plus preview and manual generation, private templates, and owner-only retry/delete
- `admin`: all reporting actions, shared templates, and schedules
- Personal API tokens use `read:reports` and `write:reports`.

Generated reports are shared records. Reporting filters therefore cannot use private per-user read or starred state. Article text, notes, and mutable article state are not copied into the report response. The report stores the bounded evidence excerpts, metadata, tags, IOCs, and source URLs actually used during generation.

New report plans prioritize publisher summaries and extracted article text over
prior AI summaries. A prior summary contributes only when its successful source
provenance matches the current evidence and its enrichment is ready. Otherwise,
the plan falls back to primary evidence and discloses that fallback in its coverage
warnings. Source freshness checks do not establish that an AI summary is factually
correct; reused summaries are explicitly labeled as prior AI output. Saved report
evidence remains an immutable snapshot of the inputs used for that report.

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

Template refreshes preserve the current draft. If the loaded template changes or
disappears, the builder shows that state; **Load latest** or deliberate template
selection refreshes the draft after confirming unsaved changes. Template saves
use the loaded revision. A generation response opens its report automatically
only when the submitted draft is still current; otherwise newer edits remain
open and the generated report is available in the library.

## Report Library

The library pages through every report the account can access, 25 at a time,
using Next/Previous controls. Search title words, `"quoted phrases"`, alternatives
with `OR`, exclusions such as `-test`, or an exact report UUID. Search uses the
PostgreSQL `simple` text configuration (case-insensitive words without stemming).
Filters cover status, exact report type, trigger, and creation dates. The through
date includes its full UTC day. Changing filters returns to page one.
Title/ID search and report type apply when you select **Search reports**.
Changing status, trigger, or dates preserves unsubmitted search text.
**Clear report filters** resets both applied filters and unsubmitted search fields.

Navigation uses `(created_at, id)` keysets and a first-page time cutoff, so newer
reports do not shift later pages and deleting the previous page's last report
does not break continuation. **Refresh** returns to page one with a new cutoff.
The page shows its current result count, without an expensive global count.
Polling updates report statuses; current permissions, deletions, status changes,
and deliberately backdated inserts can still change membership. This is not a
database snapshot held open across browser requests.

`GET /v1/reports/library` returns `items`, `current_cursor`, `next_cursor`, and
`as_of`. Send `next_cursor` unchanged with the same filters to continue; retain
each page's `current_cursor` to revisit it. Cursors describe positions, not access
grants: every request checks current permissions. Changed filters or principal,
malformed cursors, and invalid date ranges return HTTP 422. `created_from` is
inclusive and `created_before` exclusive. Limits are 25 by default, at most 100.
The original offset-based `GET /v1/reports` remains available for existing clients.
Migration `0088_report_library_indexes` adds keyset and GIN title-search indexes;
plan for index-build I/O and a write lock when upgrading a large report library.

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
11. validates each evidence quote against its supplied batch and requires citations on narrative paragraphs, list items, table rows, and key points

New generations reject unknown citations and quotations absent from the exact
bounded excerpt. Each finding must include `evidence_quotes` objects with a
`citation` and an exact 12–2,000 character `quote`; whitespace differences are
normalized. Section citations must refer to findings actually included in that
section's prompt and match the identifiers used in the narrative. Code and link
labels cannot masquerade as source citations. Invalid responses consume the
normal bounded retry/call budget and are recorded as failed provider attempts.

These are structural provenance checks, not semantic verification: a matching
quotation does not prove a generated conclusion follows from it. Review source
evidence before acting. Empty evidence batches add explicit coverage warnings;
if all batches are empty, narrative sections disclose insufficient evidence and
require no further provider calls. Source titles are never substituted for
missing findings. The report view displays checked finding/claim-block counts
and incomplete synthesis. Existing reports remain readable without claiming
these newer checks were performed. Optional context and findings compaction
remain visible through coverage warnings.

If section prompt compaction removes every verified finding, that section gets
an explicit context-budget warning and makes no provider request. Increase the
selected model's context allowance or reduce the output reserve to fit evidence.
Report token totals summarize completed stages; per-attempt usage events and the
provider usage view also account for failed paid retries. Missing provider usage
remains unknown in that view rather than becoming a billing estimate.

Configure these limits in **Settings -> AI -> Report Context Guardrails**. Set **Model Context Window** to the actual context supported by the loaded model and runtime, not the model family maximum. Conservative starting points are:

| Model context | Initial report completion tokens | Safety margin | Source cap |
| --- | ---: | ---: | ---: |
| 2K | 256 | 5-10% | 200-300 |
| 4K | 512 | 10-15% | 300-500 |
| 8K | 800-1,200 | 15-20% | 500-700 |

**Initial report completion tokens** controls every evidence-batch and section
call independently of the provider's default completion setting. It also reserves
that output space when planning report input. The setting accepts 256–131,072
tokens and keeps the existing API name `report_reserved_output_tokens`; saved
values and the 1,200-token default are unchanged. Evidence batches use the same
configured starting allowance as report sections.

When a provider reports output truncation, ThreatLens can increase the allowance
on a bounded retry, within the exact context headroom left by that serialized
prompt. The retry ceiling is the greater of the report budget or provider default,
capped at 131,072 tokens. For example, a 16,384-token report budget can be used
with a 5,000-token article/brief default when the model and context window support
it. Configure both values within the selected model's actual output limits;
ThreatLens does not infer vendor-specific limits from the model name.

If a report still truncates at a small retry allowance such as **1,588 tokens**
after raising a named provider's default to **131,072**, check **Saved report
budgets** beside the provider controls in **Settings -> AI -> Configuration**.
Provider changes do not update the shared report settings. For example, an
8,192-token context with a 15% safety margin (1,229 tokens), 384 tokens of
protocol overhead, and a 4,991-token estimated prompt leaves only
`8,192 - 1,229 - 384 - 4,991 = 1,588` tokens for output. The initial report
allowance can still be the saved 1,200 tokens. Follow **Review report budget
controls**, set the context window and initial completion allowance within the
actual report model's limits, and use **Save changes** before retrying the report.
The summary shows saved values and identifies unsaved report budget edits.

Keep AI worker concurrency at `1` for memory-constrained local inference. These
are admission-control settings, not quality guarantees; very small models may
still struggle to return valid structured JSON or follow citation instructions.
Response-byte limits and request deadlines continue to apply to larger outputs.

The exact company context and global instructions are frozen when a report is queued, so later edits do not change the durable snapshot. Before each provider call, ThreatLens builds a bounded working projection from that snapshot. It preserves the objective and global instructions first, then fits custom instructions, topic lists, structured company fields, and profile text into the remaining prompt allowance. Compaction is recorded in report warnings.

Planning reads bounded text and summary prefixes directly from PostgreSQL using
the configured source token cap. It retains citation metadata and selected
evidence, discarding body copies and excluded evidence. Candidate payloads share
a 32 MiB planning budget, with individual database batches capped at 8 MiB;
exceeding the budget asks you to exclude a source or narrow the selection.
Text-availability and coverage counts still reflect the original articles.

The worker revalidates the current provider, model, context limits, and model-call ceiling at execution and retry time. If a queued report was planned for a larger model, execution tightens excerpts and omits only lower-ranked sources until the current limits fit, then records the changed coverage. It fails before a provider call only when the required protocol, objective, enabled AI sections, and one evidence unit cannot fit at all. Provider usage, exact planning telemetry, stages, model-call counts, and failures appear in AI task history and worker logs.

## Templates And Schedules

Built-in templates are immutable and can be cloned. Analysts can maintain private templates; only administrators can create or update shared templates.

Administrators can schedule weekly or monthly reports with:

- an IANA time zone and local execution time
- previous complete week, previous complete month, or rolling-day windows
- latest-only, skip, or bounded catch-up behavior (maximum four runs). `skip`
  still dispatches a normal tick up to five minutes late, including a current
  tick reached after older missed ticks. Older unstarted ticks are skipped.
  `latest` uses the most recent scheduled time, so rolling periods do not drift
  with dispatcher latency. An already attempted tick keeps its bounded retries
  even after the five-minute grace; manual runs remain available for every policy.
- optional schedule-specific instructions
- empty-period handling
- optional integration delivery and content mode

A schedule editor keeps the resource version captured when editing starts.
Background list refreshes cannot advance that version underneath unsaved fields.
If another administrator changes the schedule, the editor warns about the newer
version, and the server rejects a stale save. The draft remains available after
a conflict or failed refresh. Cancel and edit again to deliberately load the
latest values before reapplying local changes. Fields are disabled while a save
is pending.

Dispatchers recheck retry time and schedule version after acquiring the schedule
row lock. A failure is recorded only against the version and scheduled tick that
was attempted; a delayed dispatcher cannot overwrite a newer retry, edit, or
successful reservation.

Calendar windows are calculated in the configured time zone, including daylight-saving transitions. Generation keys make scheduled periods idempotent.

## Artifacts And Delivery

Ready reports can be downloaded as Markdown, standalone HTML, or PDF. Artifacts are rendered from the persisted report snapshot rather than regenerated by AI.

The web report view renders headings, nested and ordered lists, tables, emphasis,
quotes and code. Included `[S#]` citations link to the frozen source evidence and
move keyboard focus there. Generated HTML is ignored, images appear as omitted
image descriptions, and external links require a deliberate click. Existing
reports use this rendering without regeneration.

HTML and PDF share a bounded Markdown parser and support headings, nested and
ordered lists, tables, emphasis, quotations, code, safe links and source anchors.
They retain coverage disclosures and never fetch images or other external
resources while rendering. Oversized documents fail with a clear download limit
and a Markdown alternative. See the [export formatting contract](../reference/report-export-format.md)
for limits, font coverage and PDF accessibility constraints. The Markdown
download preserves section content and adds the same coverage disclosures.

When delivery is requested, the ready-report transaction writes one idempotent `report_ready` integration event. Existing SMTP and webhook hooks can subscribe to this event and retain generic delivery attempts, retries, circuit state, dead-letter replay, and metrics. Set `PUBLIC_APP_URL` so email and webhook templates receive an absolute `{{ brief.url }}` link.

## Failure Recovery

- Report creation and retry accept `Idempotency-Key`; an exact replay returns the original report while a conflicting payload is rejected.
- The web client keeps each tab's unresolved mutation keys in session storage until a definitive response or authentication reset. Server-side idempotency and resource versions remain the correctness boundary. Write failures degrade to an in-memory key with a reload warning; unreadable storage fails before dispatch because an unresolved key cannot be ruled out safely.
- Report and AI task state commit before queue publication. A broker exception has an unknown outcome, so durable queued work is retried with the same task identity and capped exponential backoff instead of being marked failed. Once publication is confirmed, ThreatLens trusts the persistent broker and does not emit periodic duplicate messages while a task waits for an AI worker.
- Worker redelivery cannot make a second provider call while the original renewable generation lease is still owned. A superseded worker cannot persist sections or terminal state after ownership moves.
- Invalid template or context-budget configuration failures use capped retries and then quarantine the schedule. Transient planning failures use capped exponential backoff; after exhaustion, ThreatLens records the failure and advances to the next occurrence so one schedule cannot starve healthy schedules.
- Canceling a report from **Settings -> AI -> Activity** settles both records; generation also checks for cancellation between model calls.
- Lost workers for a running report are reconciled into a terminal failure. A durable report that has not started remains queued and changes to `waiting_for_worker` when no active worker consumes `ai-reports-v2`; it resumes automatically after that subscription returns.
- Provider and context errors retain actionable messages; unexpected exception details stay in worker logs while the UI receives a sanitized recovery message.
- Adaptive context decisions log usable input, fixed prompt size, peak serialized input, batch count, selected sources, omitted sources, and whether optional context was compacted.
- Failed or skipped reports can be retried by their owner or an administrator from the immutable source snapshot.
- Queued and running reports cannot be deleted.
- Scheduled empty periods are retained as skipped report records when **Skip periods with no sources** is enabled.

Operators can tune durable dispatch, schedule retry, generation leases, and rolling-upgrade grace with the `REPORT_*` and `CELERY_VISIBILITY_TIMEOUT_SECONDS` settings documented in the configuration reference. Keep the broker visibility timeout longer than the maximum expected report run to reduce duplicate queue load. If a run exceeds it, redelivery reuses the stable task ID and waits behind the renewable generation fence instead of repeating owned provider work. Ownership waits remain unbounded because another valid worker can still finish, while startup, ownership-verification, and settlement faults use a separate bounded exponential retry budget and become a durable task/report error when exhausted. During an upgrade, an unfenced `running` report from an older worker receives a 24-hour compatibility lease by default so the new worker cannot duplicate provider calls. Queued work published by an older binary is atomically superseded with a new task-run identity before it enters `ai-reports-v2`; a delayed message on `ai` then sees its original run as terminal and exits.

Do not roll the AI worker back while `ai-reports-v2` contains work. Stop report-producing API and maintenance processes, let the current worker drain that queue, and only then replace the worker binary. The documented local worker command consumes both `ai` and `ai-reports-v2`.

Report task-lineage migrations are additive, but enabling supersession has one strict upgrade boundary: stop report-producing API and maintenance processes, drain old report publishers, apply the migrations, and only then start the new API and maintenance processes. An old binary must not create report rows after the lineage migration has finished because it cannot populate the canonical task pointer. This ordered handoff prevents a late legacy enqueue failure from overwriting replacement state without adding a permanent database trigger for one deployment transition.

Migration `0053_report_operation_receipts` is additive and preserves its receipt data on downgrade, so the previous backend can run while the table remains present and a later re-upgrade retains accepted keys. In a rolling deployment, migrate first, replace all API replicas, and then publish the matching web bundle: older API replicas do not understand idempotency headers for template, clone, or schedule creation and therefore cannot provide retry deduplication for those new UI requests.

Use `docker compose logs -f worker-ai` for generation diagnostics and the report detail plus **Settings -> AI -> Activity** for persisted stage/provider history. If a report shows **Waiting for an AI report worker**, update the Compose file and recreate the AI worker, then verify that both AI queues are listed:

```bash
docker compose up -d --force-recreate worker-ai
docker compose exec -T worker-ai celery -A app.tasks.celery_app.celery_app inspect active_queues
```

`GET /health/worker` also reports `ai-reports-v2` in `queues.missing` to authenticated administrators when the deployment is using an outdated worker command.

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

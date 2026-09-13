# AI implementation review — 2026-09-12

Follow-up: the requested provider capabilities, durable recovery, grounding,
provider operations, export formatting and Vitest changes are recorded in the
[AI hardening implementation assessment](2026-09-12-ai-hardening-assessment.md).
The findings below retain the original review baseline and are not a current
list of unimplemented work.

This review follows the Gemini endpoint and report completion-budget fixes. It
covers the implemented AI workflows, from configuration and authorization through
provider calls, durable processing, generated artifacts, and browser display.
It distinguishes reproduced defects from capabilities that the current adapter
does not implement. The baseline is `34a0acc` on `dev`.

## Feature inventory

| Feature | Current implementation | Main boundary or limitation |
| --- | --- | --- |
| Provider settings | Paginated named profiles, independent encrypted credentials, default and per-feature routing, versioned edits and queued selection snapshots; legacy settings remain supported. | One OpenAI-compatible chat-completions dialect; a vendor label does not enable native APIs or declare model capabilities. |
| Article enrichment | Optional summary and company-specific relevance score/reasons; manual, automatic and scoped reprocessing. Source/configuration fingerprints avoid redundant work. | Summary and relevance share one provider assignment. Article input uses character caps, without the report planner's context calculation. |
| Daily briefs | Organization-specific narrative, key points and actions over selected items; scheduled/manual generation, history and historical backfill. | Item-count and character caps bound input size, but do not establish that it fits a particular model. |
| Reports | Frozen sources and instructions; bounded evidence batches and section calls; deterministic source, scope and IOC sections; schedules, retry/cancel, library, exports and delivery. | Context limits remain shared report settings. Citation identifiers are filtered, but factual support is not verified. |
| Operations and recovery | Task history, parent/child reprocessing progress, usage and failure aggregation, cancellation, stale-worker reconciliation and durable provider-attempt receipts. | Ambiguous external outcomes require reconciliation. Reports share the default AI worker's capacity with enrichment and briefs. |
| Governance | Current IAM/data-policy fences before external I/O; lineage-aware history and aggregates; endpoint validation, pinned destinations, deadlines, decoded response limits and sanitized diagnostics. | Enforcement depends on the configured data-policy mode. Network and authorization safeguards do not establish the truth of generated text. |
| MCP and new enrichment | ADR for a future authenticated, initially read-only MCP adapter. | No MCP server, external MCP tools, retrieval assistant, independent entity/ATT&CK extraction or automatic cross-provider failover is implemented. |

## Findings and remediation

| ID / priority | Reproduced defect | Remediation |
| --- | --- | --- |
| AI-F01 · P2 | The report web view split Markdown into plain paragraphs, leaving headings, tables and emphasis visible as syntax. | `febeb5d`: CommonMark/GFM rendering, bounded-width tables/code, safe links and keyboard-operable source citations. Raw HTML is ignored and images do not trigger external requests. Existing reports render without another AI call. |
| AI-F02 · P2 | A successful AI settings save cleared dirty state before updating the real query cache; hydration restored the old model/token values during refresh. | `d48e53d`: publish the accepted PUT result to the cache before clearing dirty state. Deferred and failed refresh regressions verify that the next edit/save keeps the accepted budgets. |
| AI-F03 · P2 | The dashboard omitted `brief_text`, including briefs whose only useful output was the narrative. | `1a52ac8`: render the selected narrative with paragraph breaks and escaped text; keep empty narratives unobtrusive. |
| AI-F04 · P2 | Empty objects and malformed feature fields could be persisted as ready enrichment/brief results; nonfinite relevance scores could poison JSON serialization. | `4e8d2eb`: validate enabled feature outputs before settling provider success. Invalid output follows the existing bounded retry path and records an actionable failure. Optional token counts reject nonfinite, negative and out-of-range values. |
| AI-F05 · P2 | Failed/truncated responses discarded provider-reported usage, including tokens consumed by reasoning. | `4e8d2eb`: retain valid failed-response token counts and latency in usage events. Counts are not invented when the provider omits them; historical records are not rewritten. |
| AI-F06 · P2 | The private-address transport constraint applied to named HTTP profiles but not legacy HTTP endpoints. | `4e8d2eb`: apply the same pinned private-network destination restriction to both paths. Legacy credential-origin restrictions remain in place. |
| AI-F07 · P2 | A named connection test used a fixed 128-token limit but truncation advice told users to increase saved feature budgets, which cannot change that test. | `f04da0c`, `0e8344c`: explain that the endpoint responded and the bounded diagnostic exhausted its allowance. Keep functional success false and do not increase cost or retry automatically. |
| AI-F08 · P2 | Explicit provider refusals and content-filter stops became generic missing-content errors, retried the same rejected input, and could lose the useful failure category. | `cffd480`: classify them before JSON/length handling, stop automatic retries and retain usage. The displayed diagnostic does not echo arbitrary provider refusal text. |
| AI-F09 · P3 | A 429 message mentioning an API key was classified as nonretryable by the client even though the runtime subsequently retried by HTTP status. | `4e8d2eb`: HTTP status controls the client retry flag. This corrects inconsistent diagnostics; it was not a missing-runtime-retry defect. |
| AI-F10 · P2 | A duplicate daily-brief delivery could mark the original owner's run skipped, including after acquiring the Redis lease but before recording the database claim. | `2ec11a9`, `d7ba4a3`: preserve running/terminal owners and defer queued deliveries using the same task identity. The failed-lease branch does not terminalize an owner in the preclaim window. Independent review prompted a second regression covering that window. |
| AI-F11 · P2 | Daily-brief backfill recomputed its dates after redelivery, so a worker crash across midnight could substitute a new day and omit the oldest requested day. | `cbc381c`: persist the parent reference time before generation; reuse child reference metadata for legacy interrupted parents, then durable creation time as fallback. Cross-midnight recovery tests cover both forms. |

## Remaining improvements

| ID / priority | Area | Evidence and impact | Recommended improvement |
| --- | --- | --- | --- |
| AI-R01 · P2 | Model capabilities and budgets | `ai_provider_client.call_ai_json` constructs a common chat-completions request with `max_tokens`/temperature and prompts asking for JSON output. Profiles have no dialect, context-window, reasoning or supported-parameter contract. Report budgets are shared; article and brief prompts have character limits instead of model-context preflight. A saved endpoint or successful connection test cannot establish compatibility for every feature. | Add explicit, versioned capability settings and adapter contracts. Validate input plus output against the selected model, expose reasoning/output controls where supported, and qualify representative requests for all three feature routes. Keep model aliases and provider limits operator-reviewable rather than infer them from a hardcoded vendor name. |
| AI-R02 · P2 | Report grounding | `report_generation._normalize_findings` discards unknown citations; `_synthesize_evidence_batches` falls back to source titles if no findings survive. `_generate_section` accepts nonempty text with no valid citations, and `_remove_unknown_inline_citations` removes the marker while retaining the claim. Synthetic probes confirmed these paths. | Distinguish an intentionally empty evidence result from malformed output; make degraded synthesis visible. Validate section response types and citation coverage, retain unsupported-claim warnings, and add a source-grounded evaluation corpus. Identifier validity alone must not be presented as factual verification. |
| AI-R03 · P2 | Provider observability and admission | Usage/model summaries group by model rather than named profile ID/version. The same model name on different endpoints is combined. `ai_ops_metrics._build_endpoint_health` counts the substring `timeout`, which misses actual total/DNS deadline errors and some wrapped ambiguous outcomes. Retry delays do not honor provider-wide rate state or `Retry-After`; profiles lack independent concurrency and cost budgets. | Attribute immutable usage to profile/version and persist typed failure categories. Track provider and feature backlog age, honor bounded provider retry advice, and add per-provider admission, token/cost limits and cooldowns. Preserve ambiguous-attempt fences instead of introducing blind failover. |
| AI-R04 · P2 | AI capacity isolation | Compose's `worker-ai` consumes both `ai` and `ai-reports-v2`, with concurrency one by default. A long report can delay article enrichment and daily briefs even though ingestion has separate workers. | Define freshness objectives per feature and measure mixed workloads on the target inference hardware. Reserve capacity or use fair admission between reports and short requests without overloading a local model. |
| AI-R05 · P2 | Artifact format consistency | `report_rendering._markdown_fragment_to_html` recognizes unordered lists, bold and code only. A synthetic heading/ordered-list/table probe retains literal syntax. PDF generation strips a subset of syntax into plain paragraphs. | Use a tested common Markdown contract for HTML and PDF exports, including tables, nested lists, code and links. Preserve escaping, safe URL handling and the absence of automatically fetched external resources. |
| AI-R06 · P2 | Quality and provider qualification | The suite has extensive deterministic transport, database and lifecycle tests. It does not establish relevance calibration, hallucination rates, prompt-injection resistance or output quality for each hosted/local model configuration. | Add opt-in, budgeted evaluations with versioned synthetic/public fixtures, expected evidence and multilingual/adversarial cases. Compare model and prompt changes before release; record context/output limits, latency, quality and failure recovery. |
| AI-R07 · P2 | Long queue recovery | `ai_ops._reconcile_stale_ai_runs` permits queued non-report work to be terminalized after one hour even when it is waiting in the broker. Worker inspection does not enumerate that backlog. With one AI slot, a 100-item reprocess averaging 40 seconds per item can reach the threshold. Reports already have a separate durable waiting policy. | Track durable admission/publication and distinguish an unconsumed message from a lost worker. Preserve accepted selections and resume them after capacity returns; test queues paused beyond the current timeout. |
| AI-R08 · P2, conditional | Historical malformed output | New writes now reject nonfinite scores, but existing malformed scores can still reach item/AI serializers and relevance aggregates (`items.py`, `ai_reporting.py`, `ai_ops_metrics.py`). No such deployment record was demonstrated or modified during this review. | Add finite-value read guards and a bounded, audited repair for affected historical rows. Keep unknown/invalid scores distinct from low relevance. |
| AI-R09 · P3 | Event chronology | `AITaskEvent.created_at` uses a PostgreSQL transaction timestamp; `ai_ops` orders events only by that field. Several same-transaction events can tie, reproduced by an existing runtime test. | Introduce durable per-run event sequence numbers; use timestamp plus stable ordering for presentation without assuming UUID order proves chronology. |
| AI-R10 · P3 | Maintainability | `ai_integration.py` and `ai_request_runtime.py` remain close to the 1,200-line source gate. The stable facade still coordinates many callbacks and shared failure/settlement assumptions. | Extract cohesive connection, enrichment and brief orchestration behind explicit typed contracts. Expand dependency-boundary and generated lifecycle tests as each module moves; avoid splitting state transitions merely to satisfy a line limit. |
| AI-R11 · P3 | Development dependencies | The dependency audit reports the existing moderate Vitest/`@vitest/mocker` advisory; no new Markdown dependency advisory was reported. The fix requires a newer Vitest major. | Upgrade and qualify the test runner separately. The advisory concerns development-server mocking; it is not evidence that the deployed static web image exposes that surface. |
| AI-R12 · P2 | Article reprocess redelivery | `item_ai_tasks` reselects items and creates new forced child runs after redelivery; parent progress counts children rather than distinct items. An isolated queue/progress probe produced `[A, A, B]` after a crash following A, and completing both A children terminalized a two-item parent early. Shared attempt roots block repeating settled provider I/O, but duplicate work can regress ready enrichment to pending/error. | Persist the selected item set and one child identity/outcome per parent and item. Resume uncompleted work and preserve settled successes; test crashes during selection, publication and child completion. |

The capability limitation is substantive: [OpenAI's Chat Completions contract](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
documents model-specific output parameters, while [Google's compatibility guide](https://ai.google.dev/gemini-api/docs/openai)
documents reasoning configuration. A common URL shape does not imply that every
model accepts the same request fields. The [Vitest advisory](https://github.com/advisories/GHSA-82fw-gwwq-j7x9)
identifies patched releases and its development-server preconditions.

## Invariants reviewed

- Legacy credentials remain bound to the configured HTTPS origin; named profiles
  never inherit those credentials. Retaining a key while changing its origin
  requires an explicit credential decision.
- Queued named-provider work keeps an ID/version selection and fails before I/O
  if that selection is unavailable or changed. Routing changes do not silently
  redirect an already prepared request.
- Provider I/O retries remain bounded by provider attempts and report model-call/context
  budgets. An ambiguous or unsettled receipt must not be treated as permission
  to repeat an external request.
- Lease-contention retries may continue while another owner remains active;
  they do not constitute additional provider I/O. The existing non-report
  queued-work grace period remains the separate limitation in AI-R07.
- Cancellation, worker ownership, IAM and data policy are rechecked at the
  established execution boundaries. Request deadlines must not be implemented
  by releasing authorization locks while an untracked request keeps running.
- AI-generated text is untrusted display content. Rendering must not execute
  model-supplied HTML or load tracking images, and citation navigation must remain
  usable with a keyboard.
- Settings saves and background refetches must preserve the submitted baseline
  and any newer local edits. A successful network response alone is not a test
  of the editor lifecycle.

## Validation and limits

Independent frontend, provider/runtime and workflow reviewers contributed to
this review. Root also inspected report planning, citation handling, exports,
governance predicates and deployment boundaries. Synthetic probes used isolated
configuration without a live provider call.

| Check | Result |
| --- | --- |
| Frontend regression | 1,008 tests in 116 files passed at the frontend worktree's `4541f19`, together with lint, TypeScript and a production build. Root subsequently wrapped an identical class list for readability. |
| Integrated browser checks | At frozen `cbc381c`, all three report workflows passed in Chromium, Firefox and WebKit, including keyboard citation focus, automated article accessibility checks, mobile width and no automatic external image requests. Root inspected the synthetic desktop rendering. |
| Integrated frontend checks | Full lint, browser TypeScript compilation and the configured high/critical dependency audit passed at `cbc381c`. The audit still reports two moderate entries for the existing Vitest advisory. |
| Broad backend regression | 50 AI/report/worker test files selected 722 cases at frozen `cbc381c`: 721 passed, one old success fixture failed because it used `{"ok": true}` as a daily brief. The fixture now returns valid brief text while preserving its receipt/audit assertions; all 19 cases in the affected egress and connection suites passed afterward. Production code did not change after the broad run. |
| New boundaries and recovery | Focused provider/client, feature-output persistence, durable receipts, refusal handling, duplicate-delivery ownership and backfill-anchor regressions passed. They cover reasoning-only truncation usage, nonfinite/overflow counts, required output fields, private HTTP parity, the preclaim race and new/legacy cross-midnight recovery. These cases overlap the broad regression and are not additional unique coverage counts. |
| Static checks | Ruff on changed backend modules/tests, the source-size gate for 703 production files and whitespace checks passed. |

Provider calls in the regression suite use controlled transports; database and
queue tests use disposable services. The review did not change deployment keys,
saved provider/report budgets, historical AI output or user-owned lock/backup
files. No additional paid model request was needed to fix display rendering.

Failure-path review covered preparation and authorization before I/O, response
validation, ambiguous transport outcomes, receipt settlement after database
failure, cancellation between report stages, duplicate worker delivery and
partial backfill recovery. The queue/reprocess gaps above remain open; a passing
test suite is not a claim that those workflows now resume durably.

This is an implementation review and targeted remediation, not proof that every
model response will be correct. The previously recovered Gemini report is useful
evidence for that saved configuration; it is not a provider compatibility matrix,
sustained capacity result or manual assistive-technology assessment.

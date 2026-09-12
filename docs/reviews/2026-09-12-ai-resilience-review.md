# AI resilience and code review — 2026-09-12

Reviewed revision: `b979c30`, branch `dev`. This is a new review of the completed
AI hardening implementation. Findings below describe the code at that revision;
they are not a list of fixes implemented by this review.

The review found **12 P2 defects and one P3 defect**. The highest priorities are
execution ownership, cancellation, connection-test authorization, provider-output
persistence and the provenance of reused AI summaries. No P1 issue or article-data
authorization escape was demonstrated in this pass. That is a statement about the
evidence collected, not an assurance that other defects cannot exist.

Three independent reviewers examined provider/runtime behavior, durable workflow
recovery, and report/UI behavior. The primary review covered their integration,
source selection, capacity, architecture and independent reproductions. Inspection
was deepest in the AI subsystem, with surrounding IAM/data-policy, ingestion,
retention, exports, deployment and frontend contracts sampled where they affect
AI. This was not an exhaustive review of every unrelated application module.

**Invariants used to assess the implementation**

- An accepted logical operation survives queue outages without gaining permission
  to repeat an ambiguous external request.
- Only the current execution may defer, finish or publish its logical task.
- Cancellation records durable intent, uses consistent lock ordering and
  eventually reaches a terminal state even if no worker consumes the message.
- Current authorization and the selected destination remain valid before each
  governed external request, including diagnostics.
- A received response remains distinguishable from an uncertain transmission;
  malformed content or optional metadata must not corrupt that distinction.
- Derived summaries retain their actual source version and status. Report
  citations refer to usable evidence and remain visible in rendered output.
- Materialization, database work and synchronous HTTP requests have limits that
  agree across the application, worker and proxy.
- An asynchronous result updates the state it was submitted from, preserving
  later edits and a usable error-recovery path.

**Confirmed findings**

| ID / priority | Area | Finding and demonstrated effect | Recommended correction |
| --- | --- | --- | --- |
| AR01 · P2 | Authorization | The legacy connection-test route omits the actor authorization context. Revoking its API token after acceptance still allowed one provider request; the named-provider route correctly sent zero. The request contained synthetic diagnostic content, so this demonstrates unauthorized continuation/spend, not article disclosure. [Route](../../backend/app/api/routes/ai.py#L279), [optional fence](../../backend/app/services/ai_integration.py#L1019). | Require the same actor fence for both routes and every retry. Test revocation immediately before first I/O and between attempts. |
| AR02 · P2 | Worker ownership | A previously started worker can resume after recovery assigns a replacement delivery, then return that replacement's running task to queued. Delivery identity is checked at entry, but deferral accepts only the logical run ID. [Deferral](../../backend/app/services/ai_workflow_dispatch.py#L106), [item-busy continuation](../../backend/app/tasks/item_ai_tasks.py#L62). | Pass an expected execution generation/delivery through continuation writes and reject outdated deferral, completion and publication. Preserve independent receipt safeguards. |
| AR03 · P2 | Concurrency | The actual API cancellation service locks a reprocess parent before its children; child completion locks the child before updating its parent. Concurrent execution produced PostgreSQL `DeadlockDetected`. [Cancellation](../../backend/app/services/ai_telemetry_data_policy.py#L319), [child locking](../../backend/app/services/ai_telemetry_data_policy.py#L347), [parent progress](../../backend/app/services/ai_ops.py#L648). | Consolidate cancellation paths, persist authorized cancellation intent separately, and process children through bounded transactions with a common lock order, rechecking current authorization for each batch. Verify the governed API path, not only the legacy helper. |
| AR04 · P2 | Recovery | When inspection is unavailable, canceling a published queued task can leave `queued/cancel_requested` indefinitely. Recovered inspection and advancing its age by 30 days did not settle it; the publisher refuses canceled work. A revoked or lost message never reaches the worker body that would finish it. It also retains an outstanding-publication slot. [Cancellation decision](../../backend/app/services/ai_telemetry_data_policy.py#L381), [publisher](../../backend/app/services/ai_workflow_publication.py#L75). | Add durable cancellation reconciliation independent of worker delivery. Terminalize safely fenced queued executions, settle dispatch/progress and release admission capacity. |
| AR05 · P2 | Provider boundary | Valid JSON containing escaped U+0000 passes feature validation but cannot be stored in PostgreSQL text. A summary produced a successful receipt/usage event followed by a result-write `DataError`; malformed provider model metadata instead left a reserved receipt with missing usage despite an HTTP 200 response. [Validation](../../backend/app/services/ai_output_validation.py#L16), [model metadata](../../backend/app/services/ai_provider_client.py#L371), [result write](../../backend/app/services/ai_integration.py#L394). | Validate database-safe Unicode, field sizes and JSON values before successful settlement. Classify invalid feature content as a received-response failure; sanitize or omit invalid optional telemetry while retaining known usage. |
| AR06 · P2 | Evidence provenance | Failed re-enrichment retains the previous summary, while briefs prefer it without checking status or freshness. Reports similarly label any retained summary “Existing grounded summary” and allow its text to satisfy exact-quote checks. A corrected version 2.0 bulletin was synthesized from its old version 1.0 summary. [Brief query](../../backend/app/services/ai_integration.py#L594), [brief prompt](../../backend/app/services/ai_prompting.py#L91), [report evidence](../../backend/app/services/report_sources.py#L323). | Separate the last successful result's provenance from the current attempt. Reuse only suitable, version-matching evidence; otherwise fall back to primary text and disclose stale/degraded enrichment. Do not label unverified model text as grounded source evidence. |
| AR07 · P2 | Report quality | Numeric factual table rows bypass citation presence checks because the validator ignores blocks with no alphabetic characters. A cited introduction plus an uncited `2026 / 12345 / 97%` incidents row passed with only one counted claim block. [Validator](../../backend/app/services/report_grounding.py#L130), [prompt contract](../../backend/app/services/report_prompt_budget.py#L27). | Include substantive numerical claims in coverage checks. If internally computed metrics are exempt, give them explicit typed provenance instead of excluding all numeric text. |
| AR08 · P2 | Memory | Brief generation loads complete publisher and AI summaries for its source-audit rows, then selects a smaller model-input subset and truncates those summaries to 900 characters. A 40-row probe loaded 8,000,000 summary characters; 7,000,000 belonged to the 35 unselected rows. The default audit scope is 500 rows. [Selection/materialization](../../backend/app/services/ai_integration.py#L589), [late truncation](../../backend/app/services/ai_prompting.py#L91). | Select audit metadata separately, fetch SQL-bounded text only for selected sources, and enforce an aggregate byte budget before materialization. |
| AR09 · P2 | Database scalability | Reprocess progress refresh issues one child-run lookup per unsettled member. A fresh-session refresh issued 103 SQL statements for 100 children and 503 for 500; completion repeats this scan while holding parent coordination state. [Progress refresh](../../backend/app/services/ai_reprocess.py#L179). | Update the current member directly; aggregate outcomes in SQL and batch reconciliation through joins. Add a query-count budget that scales independently of child count. |
| AR10 · P2 | Workflow UI | Child-run “Show More” grows the request limit from 188 to 208 although the API maximum is 200. For a 300-child run, the resulting 422 removes all displayed rows and both paging controls. [Query state](../../web/src/pages/useAiActivityRunState.ts#L50), [rendering](../../web/src/pages/AiTaskRunDetail.tsx#L237), [API limit](../../backend/app/api/routes/ai.py#L908). | Use bounded paginated/infinite queries. Retain previously loaded rows and an independent reset/retry control on page failure. |
| AR11 · P2 | Draft lifecycle | Reprocess scope inputs remain editable during submission, but success unconditionally clears the scope. Submitting 14 days, editing to 30 while pending, and resolving the first request reset the new draft to 7. [Success handler](../../web/src/pages/AiSettingsPage.tsx#L507), [inputs](../../web/src/pages/AiActivityOperations.tsx#L284). | Clear only if the current draft still matches the submitted snapshot, or disable the relevant controls for the request lifetime. |
| AR12 · P2 | Deployment / UX | Legacy connection tests use the saved provider timeout/retries, but the generic nginx route times out at 60 seconds. An isolated current-image test returned 504 at 60.07 seconds while its synthetic upstream completed successfully at 62 seconds. Named diagnostics already cap requests at 30 seconds. [Legacy workflow](../../backend/app/services/ai_connection_workflow.py#L23), [client](../../web/src/pages/AiSettingsPage.tsx#L452), [proxy](../../web/nginx/default.conf.template#L43). | Standardize short diagnostics across both settings paths. Run longer feature-qualification requests asynchronously and expose their durable results. Align end-to-end deadlines. |
| AR13 · P3 | Rendering | The backend accepts `The advisory is at https://example.test/[S1].` as cited text, but the web's GFM autolinker consumes the marker into an external link and leaves zero source anchors. [Backend parser](../../backend/app/services/report_grounding.py#L15), [web parser](../../web/src/pages/ReportMarkdownText.tsx#L55), [citation traversal](../../web/src/pages/reportMarkdownCitations.ts#L29). | Align the validation and rendering Markdown dialects, or normalize citation boundaries before autolinking. Verify actual source anchors in the DOM for accepted output. |

**Reproduction details and practical limits**

AR02 used the actual item task runner. The old worker was paused after its start
claim; the real stale reconciler assigned a new delivery and the replacement
claimed it. When the original worker resumed and observed a busy item, its
deferral cleared the replacement's worker identity and changed the dispatch to
pending. This demonstrated lifecycle interference without a provider call;
duplicate paid I/O was not demonstrated.

AR03 and AR04 were reproduced through `cancel_ai_task_run_for_data_access`, the
service used by the current API, with real data-access fencing and PostgreSQL
transactions. Separate legacy-helper probes found related failures, but those
are not substituted for evidence about the active API path. Lock/statement
timeouts bound failure duration; they do not make the conflicting transition
successful. A second manual cancellation can resolve AR04 after inspection
recovers, but automatic recovery does not currently converge.

AR05 exercised the real response parser, feature validation, receipt settlement
and result persistence using controlled HTTP 200 JSON responses. At the service
boundary, the summary case left a pending resource after the result write failed;
the worker's generic exception handler may subsequently turn it into a task error.
The confirmed defect is that an unusable result was already recorded as successful.
The optional-model case instead lost received-response settlement and known usage.
Neither observation means PostgreSQL accepted a NUL text value.

AR06 first ran a real failed re-enrichment against a retained successful summary,
then inspected the actual brief prompt. A separate report assembly/validator
probe confirmed that the same obsolete summary is accepted as an exact quote.
This is distinct from semantic entailment: even correct quotation checks cannot
repair evidence assembled from stale model output. Reprocessing also replaces
the attempt's source hash before success, so simply adding a status check does
not fully define the provenance of retained last-successful fields.

AR07 proves missing citation coverage, not that every numeric table is false.
AR08 measures materialized text characters, not peak process RSS; the container
OOM risk depends on actual record sizes and concurrency. AR09 measures SQL count
in fresh sessions, not latency on the intended production hardware. AR10–AR11
used a real React Query client and rendered controls, with controlled network
completion. AR13 compared backend acceptance with the actual React renderer.

**Architecture, resilience and operations assessment**

The architecture has useful boundaries: explicit provider selection/capabilities,
bounded synchronous transport, an independent admission pool, durable operation
receipts, immutable reprocessing membership, saved report stages, permission-aware
usage aggregation, and export/retention resource limits. These should remain the
shared infrastructure for further AI features. No automatic cross-provider
fallback should bypass a selected destination or an unresolved paid attempt.

The main technical debt is transaction and lifecycle ownership spread across
multiple orchestration paths. The governed and legacy cancellation implementations
have diverged; fixing one helper did not fix the route actually called by the UI.
The current dependency tests prohibit service/worker imports of HTTP composition,
but do not enforce execution-generation checks or centralize cancellation.
`ai_request_runtime.py` contains a 399-line function and
`ai_integration.py` a 387-line function. Passing the 1,200-line file-size gate
does not make their commit, retry and exception paths easy to inspect. Prefer
small typed transition contracts and explicit transaction owners, introduced
alongside AR02–AR05 regression tests, over a broad cosmetic split of files.

Provider admission currently limits each profile, whereas the default AI worker
has one execution slot consuming both `ai` and `ai-reports-v2`. A long report can
therefore delay enrichment even when it uses a different provider. Reserve
capacity for short AI work or dispatch report stages fairly, then measure mixed
workloads before increasing concurrency and database pools. This is a deployment
capacity improvement, not a newly measured throughput failure.

Operations already track report/classification/tagging/export freshness and typed
provider failures. Extend those signals to durable enrichment/brief queues and
capacity-deferred work: oldest accepted age, cancellation age, admission wait,
reconciliation-required receipts and per-feature completion latency. Broker queue
depth alone does not represent jobs waiting only in PostgreSQL. Add explicit
operator actions for each condition: retry known-unsent work, inspect/resolve
ambiguous attempts, correct provider configuration, or cancel pending work.

The safe degraded state remains durable queued work for temporary admission or
broker failure, a visible terminal/degraded result for unusable evidence, and
operator reconciliation for uncertain provider outcomes. Recovery should never
treat a timeout, revoked message or missing worker as proof that no external
side effect occurred. User-facing errors should carry the durable run ID, typed
reason, next eligible time where applicable, and an action the operator can take.

**AI features worth implementing next**

These are feature/design opportunities, separate from the confirmed defects.
Address the ownership, cancellation and provenance findings before building
additional autonomous workflows.

| Order | Improvement | Concrete scope and completion criterion |
| --- | --- | --- |
| 1 | Model qualification and evaluation workbench | Test each feature's actual schema, input/output allowance, refusal behavior and latency with synthetic fixtures. Show declared versus tested capabilities and configuration conflicts before routing production work. Version prompts and expected-evidence datasets; compare relevance accuracy, citation coverage, abstention, cost and latency per provider/model. Include multilingual, adversarial and incomplete-source cases. |
| 2 | Inspectable evidence and claim provenance | Let analysts open the immutable excerpt and matched quotation behind a report citation. Separate primary-source text, deterministic metrics and prior AI output; retain source/version/provider provenance. Add per-claim support/contradiction review and analyst feedback. Every displayed support indicator should lead to the evidence actually checked. |
| 3 | Shared-account budgets and fair scheduling | Group profiles that share an upstream quota, honor `Retry-After`, coordinate cooldowns, reserve short-feature capacity and show estimated versus measured consumption. Add future-work configuration revisions/impact previews so cosmetic edits need not invalidate queued tasks. Verify recovery and fairness during long reports, rate limits and broker outages. |
| 4 | Structured enrichment and story changes | Propose affected products/versions, CVEs, actors, campaigns and ATT&CK mappings with exact supporting spans and uncertainty. Cluster duplicate reporting and generate “what changed” briefs with source links. Measure precision and analyst correction rates before automatically promoting these suggestions into authoritative tags or alerts. |
| 5 | Investigation assistant over permitted evidence | Answer bounded questions across accessible items, reports and investigation evidence, cite every supporting source, and say when evidence is insufficient or contradictory. Apply access/egress policy at retrieval and publication. Launch with read-only retrieval and draft recommendations; require separate governed actions for mutations or external tools. |
| 6 | Read-only MCP access | Expose bounded search, item/evidence retrieval, report metadata and permitted artifacts through shared application services. Preserve the caller's identity, scopes, handling labels, investigation access and egress controls. Gate release on authorization parity, token revocation/audience checks, pagination, cancellation and protocol interoperability tests. |

Provider qualification should distinguish accepted parameters from effective
capabilities. Google's compatible endpoint supports Gemini-specific extensions
and model-dependent reasoning mappings; Anthropic documents that its compatibility
layer ignores fields including `response_format` and `reasoning_effort` and
recommends its native API for full features. This supports adding typed native
adapters where needed, while retaining the compatible adapter for existing
profiles. A successful connection test alone cannot establish that a provider
honors a requested feature. [Google compatibility documentation](https://ai.google.dev/gemini-api/docs/openai),
[Anthropic compatibility documentation](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk).

MCP would let external AI clients retrieve ThreatLens evidence through a
consistent interface; it does not itself improve summarization or grounding.
The existing [MCP boundary ADR](../architecture/0006-ai-provider-profiles-and-mcp-boundary.md)
is an appropriate starting point. Remote MCP authorization requires tokens
intended for this resource; browser OIDC login or a provider API key is not an
automatic substitute. Keep the first release separate from outbound MCP tool
execution. [MCP authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).

**Validation and test priorities**

| Check performed in this review | Result |
| --- | --- |
| Selected existing backend regressions | 99 passed in 15.90 seconds: dependency boundaries, provider result contracts, durable workflow/concurrency, grounding/presentation/resume and feature-output validation. |
| Provider/API boundary probes | Four passed, reproducing both malformed-output paths and comparing legacy/named token revocation behavior against migrated disposable PostgreSQL and controlled transport. |
| Active workflow probes | Two API-service cancellation cases, one late-worker continuation and two query-growth cases reproduced the findings. Earlier helper-only cancellation probes were supplementary. |
| Brief/report provenance and materialization probes | Two PostgreSQL-backed service probes plus one report assembly/quote-validation probe reproduced AR06/AR08. |
| Frontend probes | Three React DOM cases reproduced paging, pending-draft reset and GFM citation rendering; the first two used real QueryClient state. |
| Numeric grounding and export edge checks | Numeric-row acceptance reproduced. Long headings, a 60,000-character code line and a 75,000-character paragraph rendered into bounded PDFs successfully; those suspected layout failures were dismissed. |
| Proxy deadline | Current web image with isolated configuration/upstream returned 504 after 60.07 seconds for a 62-second synthetic response. |
| Repository checks | Source-size gate passed for 738 production files; `git diff --check` passed before publication. |

Defect probes deliberately assert the observed broken behavior; their passing
results are evidence of reproduction, not evidence of remediation. Probe files
and logs were retained locally under `/tmp/threatlens-review-root`,
`/tmp/threatlens-review-recovery`, `/tmp/threatlens-ai-runtime-review` and the
isolated tracked archive `/tmp/threatlens-ai-review.VttHa0`. The scenarios and
measurements above remain in this document if those temporary artifacts expire.

The full previous suite results remain documented in the
[implementation assessment](2026-09-12-ai-hardening-assessment.md); this review did
not rerun the entire backend/frontend/browser suites or establish new remote CI
results. No paid provider calls, live application mutations or target-hardware
load qualification were used for these reproductions.

The most valuable additions to CI are generated transition sequences crossing
the real authorization/cancellation/worker services, Unicode and provider-metadata
boundary cases, source-version-to-prompt provenance checks, query-count/byte
budgets, and browser workflows beyond 200 children with delayed responses. Add a
shared corpus that must pass both citation validation and DOM-anchor assertions.
Model-quality evaluations and sustained mixed workloads should remain explicit,
budgeted gates; deterministic unit coverage cannot establish semantic accuracy,
provider capability compliance or production capacity.

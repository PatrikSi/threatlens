# AI hardening implementation assessment — 2026-09-12

This follow-up implements the six requested areas from the AI implementation
review. The work preserves legacy settings and named-provider routing, and adds
explicit contracts rather than guessing capabilities from provider or model names.

| Area | Implemented behavior | Practical boundary |
| --- | --- | --- |
| Provider compatibility | Both settings paths expose request dialect, optional temperature, reasoning effort, JSON object mode and explicit context/output ceilings. Preflight checks serialized input plus output; report planning obeys the tighter selected-model limit. Omitted new fields preserve existing settings. | Chat-completions compatibility only. Capability declarations must match the deployed model; native Gemini/Anthropic APIs, vendor-specific extensions and exact vendor tokenizers are not implemented. |
| Durable recovery | Accepted work and frozen article membership survive broker outages and long queues. Publication claims, bounded admission, queue/unacked inspection and delivery fencing prevent unbounded uncertain republication. Reports reuse persisted stage artifacts committed atomically with success receipts. | Unknown broker state pauses republication. Ambiguous provider outcomes and incomplete paid-result history require reconciliation; recovery does not assume that a lost response means no provider work occurred. |
| Report quality | Findings require exact quotations from supplied excerpts and valid current-batch citations. Rendered narrative blocks, list items, table rows and key points must cite included evidence. Empty synthesis and sections with no fitting evidence disclose degraded coverage without invented title-based findings. | Structural provenance is not semantic fact checking. Existing reports are readable but are not retroactively labeled verified. Source quality and model evaluation still require analyst review. |
| Operations | Usage snapshots provider identity/version/name/model and typed failures. Permission-aware SQL aggregates expose unknown usage separately and distinguish DNS/total deadlines from other timeouts. Cross-process concurrency and rolling hourly token reservations defer work before I/O; a separate bounded connection pool prevents admission starvation. | Limits are per profile, not shared API account or currency spend. Unknown usage conservatively retains its reservation estimate. Provider-wide Retry-After/cooldown coordination and billing reconciliation remain future work. |
| Exports | HTML/PDF support the report Markdown structures, citations and coverage disclosures through a shared bounded parser; external resources are never fetched. Artifact and layout limits produce actionable errors. | PDF is not tagged for assistive technology. Full CJK/RTL typography and pixel-identical web/PDF layout are not established. Markdown remains the most portable large-document format. |
| Test dependencies | Vitest and its matching packages are upgraded to 4.1.11, the patched release for GHSA-82fw-gwwq-j7x9. | Keep dependency audits and test-runner qualification in release checks. |

## Failure and authorization contracts

Current IAM, data-policy, source-access and provider-version fences remain in
force before outbound requests and saved-stage replay. Concurrency reservations
use short independent transactions; authorization locks are retained until
synchronous provider I/O has returned or failed. Reservation leases have an
absolute expiry and cannot renew a request's lifetime implicitly. Expired
reservation cleanup is globally bounded and includes retired provider profiles.

A capacity denial settles its unsent receipt before optional telemetry. A usage
write failure therefore cannot turn an unsent request into an ambiguous paid
operation. Successful report artifacts and receipts commit together; replay
checks the original stage fingerprint and does not add another usage event.
Backfill recovery checks each interrupted child's paid history, including stale
terminal children, before creating any replacement.

## Compatibility and operations

Migrations 0096–0099 add capability fields, workflow state, report stage artifacts,
provider usage attribution, budget reservations and history-pruning reference
guards. Cleanup counts the new dependent tables and drains composite-key rows
within the existing transaction budget; crash recovery validates one saved
payload at a time. Historical attribution is
left unknown because a model name cannot identify its provider profile. Budget
defaults are zero (unlimited); optional capability limits default to unset.
Downgrade guards refuse to remove active recovery state or configured budgets
that old workers could not enforce. Keep migrations and workers on compatible
versions and take a verified backup before upgrading.

See [AI workflow recovery](../pages/ai-workflow-recovery.md),
[provider usage](../reference/ai-provider-usage.md),
[runtime budgets](../pages/runtime-budgets.md) and
[report export formatting](../reference/report-export-format.md).

## Validation and remaining priorities

Regression coverage includes old and modern request dialects, omitted settings,
serialized context boundaries, typed transport failures, real PostgreSQL budget
races, broker outage and paused consumers, prefetched deliveries, cancellation,
worker-loss recovery and receipt reconciliation. Report tests use real persisted
artifacts to check that resumption preserves grounding counts and never repeats a
completed provider call. Export checks inspect actual HTML/PDF output and browser
resource requests. Provider calls in these tests use controlled transports.

Final aggregate validation and local deployment evidence are recorded after the
release checks below are complete.

The next useful investments are budgeted model-quality evaluations with expected
evidence, sustained mixed workloads on the intended inference hardware, manual
assistive-technology testing and per-account provider cooldowns. Separate report
worker capacity may be appropriate after those measurements. An authenticated
read-only MCP adapter remains a separate feature; these changes do not expose an
MCP endpoint or execute external tools.

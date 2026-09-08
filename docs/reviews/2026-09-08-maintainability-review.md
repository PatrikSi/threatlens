# Renewed code and maintainability review — 2026-09-08

Reviewed `446aff1` on `dev`, after the [seven-priority follow-up](2026-09-08-follow-up.md).
This is a new review of the resulting codebase. Earlier remediation reports remain
historical records. Application code was not changed during this review.

## Assessment

There is accumulating technical debt, principally in inconsistent lifecycle and
concurrency contracts across related workflows. The modular monolith remains a
reasonable deployment architecture. Its durable work records, authorization
fences, bounded external requests, session-scoped caches, and explicit degraded
states provide useful foundations. The defects below show where those mechanisms
are bypassed or composed incorrectly.

The latest follow-up is not an explosion in source size: production Python and
TypeScript grew from 627 files / 198,807 physical lines at `6518be5` to 643 files /
200,583 lines at the reviewed revision: 16 files and 1,776 net lines. The count of
files exceeding 1,000 lines remained 32. Size is a navigation signal, not a
correctness or cohesion measure. Several existing module splits preserve a large
implicit dependency surface, and new export code adds expensive interactions
with the existing authorization and worker infrastructure.

The table contains eleven reproduced finding groups and three structural risks.
P2 means actionable correctness, resilience, accessibility, or capacity work for
the next engineering iteration; P3 means maintenance work to incorporate as the
affected code changes. No P0/P1 finding was established by this review. That is
not a claim that every path or deployment condition has been exhaustively proven.

## Findings

| ID / Priority | Area | Finding | Recommended improvement | Evidence |
| --- | --- | --- | --- | --- |
| CR01 · P2 | Authorization / concurrency | Export publication and token revocation acquire owner/credential locks in opposite orders. Either operation can lose the deadlock. | Centralize the lock order and test publication/download against credential mutations. | Real PostgreSQL deadlocks; both victims reproduced. |
| CR02 · P2 | Reporting / concurrent editing | A refreshed schedule version is paired with stale editor fields, bypassing the intended optimistic conflict check. | Keep the version with the draft baseline; adopt a newer baseline only through explicit reconciliation. | Actual ReportingPage and deferred query-cache transition. |
| CR03 · P2 | Pipeline correctness / recovery | An older tag-reapply snapshot can overwrite tags computed from a newer article. | Use the common processing claim or source-version fence; retain recoverable incomplete-tagging state. | Real PostgreSQL interleaving restored an obsolete tag. |
| CR04 · P2 | Rule authoring / state lifecycle | Tagging saves overwrite later edits, late previews describe previous inputs, and accepted new rules can lose their selection during refetch. | Reconcile saves against submitted identity/state, fingerprint previews, and merge accepted creates before selection. | Three deferred-response probes using a real QueryClient. |
| CR05 · P2 | Navigation / UX | Dirty workspace preferences block navigation without rendering the discard/cancel dialog. | Return and render the shared dialog and test the actual router transition. | Actual controller/router remained blocked without an alertdialog. |
| CR06 · P2 | Accessibility | Hidden inputs are treated as tabbable modal controls, breaking forward and reverse focus cycling. | Use a complete tabbability calculation and cover empty modal states in browsers. | Actual DashboardDialogs reproduced in Chromium. |
| CR07 · P2 | Ingestion capacity | Hash overlap checks are quadratic; IOC persistence also performs six SQL statements per new IOC. | Remove unnecessary overlap scanning and batch normalized IOC upserts/associations with bounded work. | Deterministic comparison counts and real SQL statement counts. |
| CR08 · P2 | Retention / storage growth | An oldest prefix of oversized parents can prevent cleanup from reaching later eligible records on every pass. | Persist bounded scan progress or defer oversized parents separately, retaining dependency protections. | Real parent/child records repeatedly prevented progress. |
| CR09 · P2 | Investigation search / memory | Evidence candidates hydrate full report documents before producing short previews. | Project bounded display fields in SQL and bound merged candidate materialization. | 16,384,000 document bytes loaded for 30,000 description bytes. |
| CR10 · P2 | Download resilience / governance | Backpressured export responses have no absolute transfer deadline while holding global policy locks. | Bound response lifetime and cleanup while preserving authorization fencing. | Actual blocked ASGI response prevented an unrelated IAM write. |
| CR11 · P2 | Worker isolation / architecture | Long background exports share all default ingestion/processing worker slots. | Allocate bounded export concurrency separately or reserve processing capacity. | Current task routes and default Compose topology; saturation risk not load-measured. |
| CR12 · P2 | Export status / database capacity | Listing ready exports repeats full source-membership and authorization checks per job. | Batch authorization and source checks within the request without widening access. | 25 jobs / 10,000 shared sources produced 1,326 route-body SQL statements. |
| CR13 · P2 | Modularity / invariant ownership | Extracted workers depend on an entire mutable runtime module; related services retain cyclic and private-helper dependencies. | Introduce narrow typed workflow dependencies and shared invariant owners; enforce selected layer contracts. | Module-as-runtime facade with 69 declared dependencies and static dependency cycles. |
| CR14 · P3 | Readability / type contracts | New export service boundaries largely lack parameter/return contracts and compress complex transitions into dense expressions. | Type request, claim, authorization snapshot, state, and result boundaries; format transitions for review. | Direct code inspection; maintenance risk, not a demonstrated failure by itself. |

### CR01 — export/authentication lock inversion

[Export authorization](../../backend/app/services/export_job_access.py#L52)
locks the accepting credential before its owner. [Token revocation](../../backend/app/api/routes/tokens.py#L507)
and [session rotation](../../backend/app/services/auth_sessions.py#L361) lock the
owner first. A gated PostgreSQL interleaving produced SQLSTATE `40P01` in the
revocation transaction: revocation rolled back, the credential remained active,
and the export became ready. Reversing the selected deadlock victim let revocation
succeed but settled the export as `failed/generation_failed`.

This is a failed security operation, not evidence that a successfully revoked
credential bypasses authorization. Preserve IAM/data-policy fencing and adopt a
common principal/credential lock order. Service-account mutation routes currently
take the exclusive IAM fence first, serializing this particular conflict; an
equivalent service-account route failure was not demonstrated.

### CR02 — a schedule draft must own its version

[ScheduleEditor](../../web/src/pages/ReportSchedulesPanel.tsx#L93) initializes
field state from the original schedule. A collection refresh updates the parent
schedule object, and [submission](../../web/src/pages/ReportSchedulesPanel.tsx#L327)
spreads that newest object underneath the old editor fields. The probe opened v1,
refreshed the list after another administrator changed instructions at v2, and
saved a local name change. The request contained v1 instructions and
`If-Match: "v2"`, which would satisfy the current-version precondition. This
frontend probe captured the request; it did not exercise server acceptance.

Capture identity, baseline fields, and resource version together when editing
starts. A background refresh may notify the editor of a conflict, but must not
silently advance its precondition. Test the accepted request as well as the
rendered draft and the server's stale-version response.

### CR03 — stale tag reapplication and incomplete evaluation

[Tag reapplication](../../backend/app/tasks/item_processing_tasks.py#L474)
reads Item/Article without the normal processing claim or a source-version
settlement check. A probe paused the old reapply snapshot, committed new article
text and current tags, then resumed. The article still contained the new text,
but the old custom tag was restored. No pending processing version recorded the
need to repair that result. Apply the same item ownership/version contract as
normal classification, including tests with an existing classification.

Related, already documented recovery debt remains in
[regex result handling](../../backend/app/services/algorithm_tags.py#L337): a
temporary evaluator process-start failure yields no custom matches, and
[tag synchronization](../../backend/app/services/algorithm_tags.py#L114) removes
previously matching automatic tags. This was reproduced with an injected
`OSError`. The [execution contract](../reference/custom-regex-execution.md)
explicitly documents skipped matches and later reapply, so this is not reported
as an undisclosed behavior change. Model incomplete evaluation separately from a
definitive negative match, expose degraded tagging, and arrange bounded recovery.
Preserve evaluator limits and manual-tag semantics.

### CR04 — tagging draft, preview, and inventory lifecycles disagree

[Save completion](../../web/src/pages/useTaggingSettingsController.ts#L125)
unconditionally replaces the current draft while the editor remains editable.
[Preview completion](../../web/src/pages/useTaggingSettingsController.ts#L150)
applies results without checking the submitted input fingerprint. Clearing a
preview on edits cannot reject a response that arrives after that clearing.
[Inventory reconciliation](../../web/src/pages/useTaggingSettingsController.ts#L76)
can also clear a newly created rule immediately because its accepted ID is not
yet present in the stale list awaiting refetch. All three sequences were
reproduced with actual React Query mutations and deferred responses.

Use the existing application's draft-reconciliation conventions consistently.
Bind mutation identity to submission, retain later edits, reject stale previews,
and insert accepted creates into the cache before selecting them. This should
be one lifecycle contract with transition tests, rather than three unrelated
effect-level patches.

### CR05 — workspace settings discard dialog is never rendered

[The controller](../../web/src/pages/useWorkspaceSettingsController.ts#L232)
calls `useUnsavedChangesWarning` but ignores its return value.
[WorkspaceSettingsPage](../../web/src/pages/WorkspaceSettingsPage.tsx#L81)
renders several other confirmations, but not that hook's discard dialog. Dirty
personal or role preferences therefore block router navigation silently. A probe
confirmed the blocked router state, unchanged location, and missing alertdialog.
Render the shared dialog and validate both discard and cancel through the page.

### CR06 — modal focus calculation includes hidden elements

[The focus helper](../../web/src/hooks/useDialogFocusTrap.ts#L42) filters only
direct disabled/aria-hidden attributes. Its selector includes every input,
including the [hidden import input](../../web/src/pages/DashboardDialogs.tsx#L107)
with `tabIndex={-1}`. With no saved views, that input becomes the computed last
control. In Chromium, Tab from Import JSON moved focus to BODY, while Shift+Tab
from Close attempted to focus the hidden input and stayed on Close.

Account for visibility, negative tabindex, inert content, and disabled ancestors
when computing tabbable controls. Browser tests should include empty inventories,
conditionally hidden controls, and both directions of cycling. This is separate
from the previously fixed dialog stacking problem.

### CR07 — IOC work grows disproportionately with indicator density

[Hash extraction](../../backend/app/services/ioc_extraction.py#L139) checks each
new match against every previous span through
[`_is_overlapping`](../../backend/app/services/ioc_extraction.py#L226). For 2,000,
4,000, and 8,000 hashes, the probe counted 1,999,000, 7,998,000, and 31,996,000 span
comparisons. The largest input was only about 520 KB, below the default 4 MB
article limit. Independent elapsed measurements were 0.091, 0.231, and 0.653
seconds on this shared host; the deterministic work count is stronger evidence
than those timings. Complete word boundaries and distinct 64/40/32-character
hash lengths make these particular matches mutually non-overlapping. Remove the
redundant span scan and retain mixed-hash/boundary regressions.

The [storage loop](../../backend/app/tasks/item_processing_tasks.py#L346) then
performs per-IOC lookup/savepoint/insert work. Persisting 100 new IOCs issued 600
SQL statements: 200 SELECTs, 200 INSERTs, and 200 savepoint/release statements.
Batch deduplication and upserts while preserving uniqueness and source metadata.
Use indicator count as a workload dimension in addition to article bytes; define
visible incomplete/retry semantics if an extraction work budget is reached.

### CR08 — bounded retention scans need progress past protected prefixes

[AI-history deletion](../../backend/app/services/history_maintenance.py#L281)
and [`_delete_generic`](../../backend/app/services/lifecycle_targets.py#L615)
select the oldest bounded prefix before
[dependency budgeting](../../backend/app/services/lifecycle_dependencies.py#L67)
removes oversized parents. The next pass starts at the same oldest prefix.
[No-progress handling](../../backend/app/services/lifecycle_execution.py#L305)
retries or finishes without advancing that position.

The final probe used five real AITaskRun parents and 40,004 real child events.
Calling the AI-history handler at `batch_size=1` (the size used when one record
remains) selected four oversized parents; three passes deleted nothing and never
reached the ordinary fifth parent. The probe did not execute a durable run with
that remaining budget. The default batch size is 100, giving a 400-parent scan: persistent
starvation of full default runs requires a correspondingly larger protected
prefix. Four oversized parents do not block every default run.

Persist a bounded scan cursor or schedule oversized parents separately while
continuing past them. Keep dependency limits, ordering, and legal/retention
protections. Test repeated runs and both partial and full execution budgets.

### CR09 — investigation report previews retain full documents

[Report candidate selection](../../backend/app/services/investigation_evidence_candidates.py#L660)
selects full Report entities, including generation context and summary, before
trimming the displayed description to 600 characters. Fifty synthetic reports
caused 16,384,000 document bytes to be hydrated for 30,000 description bytes.
The candidate fan-in can grow to page × page size, capped at 1,000 per source;
its row bound does not establish a useful byte bound.

Select only required metadata and SQL-bounded preview fields, retaining the
permission predicate, ranking, and total/truncation semantics. Test increasing
document size independently of page size and measure materialization before
serialization. Apply the same projection discipline to the other candidate
source loaders where appropriate.

### CR10 — export transfer lifetime can retain global policy fences

[Download preparation](../../backend/app/api/routes/export_jobs.py#L151)
deliberately reacquires policy and credential fences for the response lifetime.
The [anonymous-file response](../../backend/app/services/export_download_scratch.py#L44)
cleans up on completion/cancellation but imposes no absolute transfer deadline.
An actual response with a gated ASGI body send stayed active while an unrelated
IAM mutation hit SQLSTATE `57014` under a probe statement timeout. Closing the
response/session released the blockage.

Preserve the authorization guarantee while bounding total response lifetime and
ensuring cancellation closes files and database dependencies. A client-side
five-minute timeout does not impose a server lifetime limit on arbitrary clients.
Default proxy buffering may absorb downstream slowness while capacity remains;
the reproduced risk is upstream/direct response backpressure, not a claim that
every slow browser immediately blocks governance through the default proxy.

### CR11 — export admission does not reserve ingestion capacity

[Task routing](../../backend/app/tasks/celery_app.py#L120) assigns background
exports to `processing`. The [default worker](../../docker-compose.yml#L277)
shares four execution slots across `default,ingest,processing`. Four distinct
principals' large exports fit the default 4 GB reservation budget and can occupy
all slots for long generation work. Distinct principals matter because the
Redis export lock serializes generation for a single owner. The configured
generation deadline is one hour.

Separate bounded export execution or reserve ordinary ingestion/processing slots.
A priority value alone cannot preempt an already running export. Add a workload
that fills export concurrency while measuring article processing and repair
freshness. This is a topology-derived contention risk; no sustained starvation
or production-capacity result is claimed here.

### CR12 — status polling repeats full export visibility work

[The list route](../../backend/app/api/routes/export_jobs.py#L98) calls
[`export_job_response`](../../backend/app/services/export_jobs.py#L123) separately
for each job. Each response rebuilds accepting authorization and validates the
source snapshot twice, against original and current access.
[Source validation](../../backend/app/services/export_job_access.py#L114)
decrypts the snapshot and issues one membership query per 500 source IDs.

For 25 ready jobs sharing 10,000 real Item rows, the probe counted 1,000 membership
queries and 1,326 route-body SQL statements, taking 10.019 seconds in disposable
PostgreSQL. HTTP dependency authentication was excluded, so this is not a full
request query count or a sustained benchmark. The UI polls every five seconds
when the visible page has active work and every 30 seconds otherwise.

Batch source membership across jobs and reuse credential/principal authorization
only within a suitably consistent request evaluation. Preserve each accepting
credential's scope, captured/current handling restrictions, source identity, and
revocation semantics; an unversioned persistent permission cache is not a safe
substitute. Add query-count and large-source polling tests.

### CR13 — module boundaries do not yet own workflow invariants

[Feed task wrappers](../../backend/app/tasks/feed_tasks.py#L636) pass
`sys.modules[__name__]` to eight extracted runners. A
[69-entry tuple](../../backend/app/tasks/feed_tasks.py#L753) explicitly keeps
dependencies reachable for legacy imports and monkeypatches. The runners receive
`ModuleType`, providing no narrow contract for the attributes they call. Keeping
stable Celery task names is useful; carrying the whole facade into every workflow
makes dependencies and responsibility harder to inspect.

A static named-module import graph found dependency groups of 18 modules around
authorization/data access/AI telemetry and 13 around notifications/integrations.
This analysis includes deferred imports: it demonstrates cyclic dependencies,
not a Python import-time crash. More concrete reversed boundaries include
[operations services calling private health-route functions](../../backend/app/services/operations_probes.py#L13)
and [export routes importing private helpers from another route](../../backend/app/api/routes/export_jobs.py#L10).

Keep public task wrappers, introduce workflow-specific typed dependencies, and
move shared principal/transport helpers below routes. Centralize item settlement
and transaction-order invariants exposed by CR01/CR03. Add narrow architecture
checks for agreed boundaries, permitting explicit adapters where necessary.
Avoid a broad service rewrite or further file splitting without responsibility
changes.

### CR14 — make new service contracts easier to review

[Export admission](../../backend/app/services/export_jobs.py#L42),
[authorization snapshots](../../backend/app/services/export_job_access.py#L31),
and [worker transitions](../../backend/app/services/export_job_worker.py#L113)
contain mostly untyped public boundaries, raw snapshot dictionaries, string state
transitions, and dense multi-field expressions. These coexist with much stronger
typed policy models elsewhere. The concern is the cost of reviewing valid state,
ownership, and exception transitions, not an objection to a particular formatter.

Introduce small typed authorization-snapshot, claim, progress, and result
contracts; spell out transition preconditions and expected errors. Add type
checking incrementally at these boundaries instead of requiring an immediate
repository-wide typing migration. Keep practical line/complexity gates, but do
not treat passing them as evidence of semantic cohesion.

## Coverage by area and next validation work

| Area | Assessment and remaining improvement |
| --- | --- |
| Architecture / modularity | The modular monolith remains suitable. Prioritize ownership of lock order, draft baselines, source revisions, and worker budgets. CR11/CR13/CR14 identify concrete boundaries to improve. |
| Code quality / readability | Ruff and source-size checks pass. Improve typed boundaries and reduce implicit runtime dependencies before adding further compatibility facades. File length alone did not determine any finding. |
| Security / authorization | Fresh authorization, encrypted artifacts, and session isolation remain useful controls. CR01 and CR10 affect security-operation availability. This review did not establish a new successful-revocation bypass or repeat a dependency advisory audit. |
| Pipeline / resilience | Durable queues and processing versions improve recovery, but alternate writers need the same ownership rules. CR03/CR08 cover stale writes and no-progress loops; incomplete regex recovery is explicitly documented above. |
| Scalability / memory | Model skew as well as volume: dense indicators, large report contexts, overlapping export sources, large dependency fan-out, and multiple exporting principals. CR07–CR12 need targeted budgets and regression measurements. |
| UI / UX / accessibility | Common dialogs, caches, and draft helpers help, but consumers still violate their contracts. CR02/CR04/CR05/CR06 need real composed transitions. Sampled investigation/alert workflows generally retain permission checks, URL context, pagination, and stale-data notices. |
| Tests / maintainability | Retain focused unit tests, but stop mocking away the critical integration behavior. Workspace controller tests replace the warning hook; tagging DOM tests replace React Query. Add real router/cache tests, two-session database interleavings, adversarial cardinality tests, and empty-state keyboard workflows. |
| Operations / deployment | Operations charts disclose gaps/truncation and the release workload harness provides reproducible evidence. Add export queue isolation, polling query counts, transaction/lock duration, retained bytes/WAL, and no-progress cleanup trends to operational measurement. |
| Documentation / feature coherence | Existing runbooks state important limits. Document shared invariants alongside implementation helpers and link each workflow to them. Complete lifecycle consistency before expanding the feature surface further. |

The next implementation order should be CR01–CR06, then the bounded-work and
contention findings CR07–CR12. Introduce CR13 contracts alongside those repairs,
with CR14 cleanup limited to touched modules. Each fix should include the
corresponding characterization scenario turned into a regression asserting the
correct outcome.

Previously disclosed deployment validation remains open: longer production-shaped
soaks and async/PDF export loads, actual deployed IdP/MFA/TLS behavior, manual
NVDA/VoiceOver checks, multi-node/partition recovery, and continuous production
trends. These are carried-forward limits from the follow-up report, not newly
discovered defects or tests claimed to have run in this review.

## Evidence and limits

Three independent reviewers covered backend failures/concurrency, frontend
lifecycle/accessibility, and capacity/operations. The primary review cross-checked
the source paths, inspected architecture/import relationships and quality gates,
and independently exercised IOC extraction.

- Six backend characterization probes ran with disposable PostgreSQL/Redis
  fixtures and migrations through `0088`. They exercised actual SQL locks,
  export publication/settlement, tag synchronization, and ASGI response lifetime.
  Broker publication and the export Redis lock were stubbed in those fixtures.
- Five frontend DOM probes used actual router or QueryClient behavior; a
  Chromium probe used the actual DashboardDialogs component. Their passing
  assertions confirm the reported broken outcomes; they do not mean the
  application satisfies the desired behavior.
- Five PostgreSQL capacity probes exercised real report hydration, IOC persistence, export-list
  membership queries, and retention parent/child records. Counts and timing
  qualifications appear with each finding. The earlier simulated dependency-count
  retention probe was supplemented by the real-child-row reproduction.
- Fresh Ruff, source-size, and diff-hygiene checks passed. The full application
  suite, cross-browser matrix, vulnerability scans, images, and GitHub Actions
  were not rerun for this source-unchanged review. Their previous results remain
  in the follow-up report and are not presented as fresh validation.
- Synthetic isolated fixtures and temporary probe copies were used. The existing
  deployment, its environment secrets/data, and user-owned untracked files were
  not used. Probe timings on the shared host do not establish supported capacity.

Temporary diagnostic logs are available locally at
`/tmp/threatlens-review-backend-probes.log`,
`/tmp/threatlens-review-workspace-probe.log`,
`/tmp/threatlens-review-tagging-probes.log`,
`/tmp/threatlens-review-schedule-probes.log`, and
`/tmp/threatlens-review-dialog-probe.log`, and
`/tmp/threatlens-capacity-review-fxpsvw9v/probe-results-final.log`.
The durable review records the triggers, observed results, source references,
and validation limitations so findings remain understandable after temporary
fixtures are removed.

# CR01–CR14 implementation and assessment — 2026-09-08

This implements the findings from the [renewed maintainability review](2026-09-08-maintainability-review.md)
on `dev`. The original review remains a historical record. Changes are committed
in coherent units as `Patrik <patrik@local>`.

## Implemented changes

| Finding | Result | Verification |
| --- | --- | --- |
| CR01 — export/credential deadlock | Export fences lock the owner before the accepting credential, matching credential mutation ordering. | Real PostgreSQL export races with token revocation and browser-session invalidation. |
| CR02 — schedule draft versions | Each editor retains its original fields and version. Conflict responses preserve the draft; deliberate reopening adopts the newer baseline. | Real page/cache transitions and a stale-save/reopen workflow in Chromium, Firefox, and WebKit. |
| CR03 — stale tag reapply | Reapply locks and reloads the current item/source. Independent durable tagging recovery preserves tags after incomplete regex evaluation, retries with bounded backoff, and exposes attention states. | Source-update interleavings, classification independence, rollback/recovery, exhaustion, access filtering, and stable repair paging. |
| CR04 — rule authoring races | Saves reconcile against the submitted draft and selection. Preview fingerprints reject outdated results. Accepted creates enter the cache before selection/refetch. | Deferred page/router/cache tests and authoring browser workflows. |
| CR05 — invisible discard prompt | Workspace preferences render the shared discard/cancel dialog for personal and role drafts. | Actual navigation, cancellation, preserved edits, and confirmed departure. |
| CR06 — modal tabbability | Modal navigation uses pinned `tabbable` with the existing stack and Escape coordination. Runtime licensing is updated. | Forward/reverse focus, hidden/disabled controls, empty states, nested dialogs, and all three browsers. |
| CR07 — IOC amplification | Hash extraction no longer performs redundant overlap scans. Batched conflict-safe IOC/link persistence preserves raw values, timestamps, and per-item evidence. | 100 IOCs: **600 → 3 SQL statements**; 1,001 IOCs: seven. Dense hash, concurrent insertion, and rollback tests. |
| CR08 — cleanup starvation | Transactional timestamp/ID cursors advance beyond oversized parents, retain budget-deferred candidates, and reset for later reconsideration. Run scan limits preserve the next anchor. | Real 40,004-child protected prefix, later-record cleanup, rollback, budget remainder, continuation, protected-only stop status, and migration checks. |
| CR09 — report preview memory | Investigation candidates select report metadata and bounded, stripped SQL previews. Full summary predicates still support search. | Fifty large report documents, tail-of-summary search, no Report ORM hydration, Unicode trimming, and bounded traced Python allocation. |
| CR10 — backpressured downloads | An absolute transfer timeout covers downstream ASGI sends as well as file reads; completion/cancellation releases authorization fences and artifacts. | Actual app stalled-send tests with PostgreSQL policy writes, plus range, disconnect, and direct-response cases. |
| CR11 — export worker capacity | `exports-v1` has its own worker, default concurrency one. Readiness, queue canaries, image tooling, and recovery service lists include it. | Queue contracts and two real worker processes: processing continues while the export consumer is occupied. |
| CR12 — export status queries | Request-local grouped authorization and bounded source caches share overlapping membership checks while preserving accepting/current authority and source identity. | Original 25-job/10,000-source case: **1,326 → 86 route-body SQL statements**, including 20 membership queries; expiry, revocation, and cache-bound cases. |
| CR13 — dependency ownership | Workers receive small typed orchestration records and immutable fetch options. Shared services own invariants; export and component-health helpers sit below routes. | Entry-point contracts, dependency-boundary checks, worker/recovery tests, and health/operations regressions. |
| CR14 — export contracts/readability | Typed claim, accepting snapshot, source, principal, and result contracts replace implicit dictionary/module boundaries. Export state transitions are formatted and separated by responsibility. | Export lifecycle, status, authorization, recovery, and transfer regressions. |

Independent reviews identified and corrected two retention composition edges:
protected-only scan limits must remain partial, and a reduced remaining batch
budget must not classify an otherwise affordable parent as oversized. Export
accept/cancel serialization also maps policy changes after commit to a retryable
access-change response. Ordinary HTTP status reads already retain their request
authorization fences.

## Deployment

Apply migrations `0089_tagging_recovery` and `0090_lifecycle_scan_cursors`, and
deploy the updated frontend with the backend. Add `worker-exports` consuming only
`exports-v1`; `EXPORT_WORKER_CONCURRENCY` defaults to `1`.
`EXPORT_TRANSFER_TIMEOUT_SECONDS` defaults to `300` and bounds transfer lifetime
after response preparation, including a slow or disconnected recipient. It is
separate from background artifact generation time.

Follow the [background export upgrade instructions](../reference/background-exports.md)
for draining old queue assignments and starting the new consumer. Run readiness
and operations checks before restarting publishers. Cursor records are internal
maintenance state; operators should use policy/run controls instead of modifying
them directly.

## Validation and practical limits

Integrated validation:

- Frontend: 918 unit/DOM tests, configured lint, TypeScript, production build and
  bundle smoke; dependency installation/audit reported no vulnerabilities.
- Browser workflows: 30 focused executions across Chromium, Firefox, and WebKit,
  plus 36 real-server authentication/accessibility workflows.
- Populated PostgreSQL 16 migration round trip: `0042` → `0090`, compatibility
  fixture preservation, downgrade to base and upgrade to `0090`; both Alembic
  drift checks passed.
- Isolated concurrent capacity smoke and failure-recovery profiles: no configured
  budget violations. These ran against application revision `644da70`.
- Recovery tooling: 84 non-destructive tests passed; the separate Docker exercise
  is verified below.
- Ruff, source-size gate, regenerated API/preview contracts, and changed-document
  links passed. The source-size gate covers 659 production files.

- Images and deployment: both local images built from tracked revision `644da70`;
  all ten Compose services passed healthchecks, proxy readiness returned HTTP 200,
  OpenAPI exposed the new tagging recovery contract, and the database was at
  migration `0090`. Worker inspection confirmed queue isolation.
- Docker recovery: backup, verification, drill, destructive restore, database
  permissions/settings, quarantine handling, and Redis flush passed against the
  new backend image. Both uniquely named deployment/recovery projects and their
  volumes were removed.

- Backend: the complete run exercised 2,235 cases: 2,230 passed, two opt-in
  capacity cases skipped, and three outdated export-queue test expectations failed.
  Those expectations were corrected; all three passed on a coverage-appending
  rerun. The affected 124 enterprise API and eight configuration tests also passed.
  This verifies all 2,233 ordinary cases without an application-code change after
  the full run. Both opt-in capacity profiles passed separately as noted above.
- Final combined statement/branch coverage is **84.36% overall** and **78.18% for
  reporting**. Every existing critical-module floor passed. Additional floors now
  protect export status (90%), export principals/transport (95%), lifecycle scanning
  (90%), and tagging recovery (95%); the six coverage-gate tests passed.

Application code was frozen at `644da70` for the integrated suite and image smoke.
Subsequent commits update test expectations, coverage gates, and documentation.
These are local validation results; no remote CI run or image publication is
claimed.
Tests use synthetic disposable services; no existing deployment or user data is
used. Frontend validation includes real asynchronous editor transitions and
three browser engines. The performance numbers above are reproducible regression
probes, not production capacity certification.

| Remaining improvement | Assessment |
| --- | --- |
| Target-hardware capacity evidence | Separate export slots preserve worker availability, but CPU, memory, database connections, and storage remain shared. Sustained production-shaped loads and recovery measurements remain necessary for deployment sizing. |
| Unique-source export status cost | Work is now shared for overlapping jobs and memory is capped. Pages of disjoint large jobs still require proportional membership work while authorization fences are held; measure this workload before increasing limits. |
| Oversized retention parents | They remain protected by design. Cursors allow other cleanup to progress; reducing or incrementally pruning their dependent history would need a separate safe lifecycle design. |
| Dependency architecture | The reviewed mutable facade and selected route/service inversions are removed. Existing large services and historical dependency cycles still warrant gradual decomposition; some private worker helpers also retain unused orchestration parameters that can be removed when those helpers are next changed. |
| Accessibility and authentication environments | Browser automation and automated accessibility checks cover the repaired flows. Manual screen-reader use, organization-specific identity providers, and deployment network behavior need environment-specific validation. |

The changes consolidate the reviewed workflow invariants and add tests at their
failure boundaries. They do not establish a universal throughput limit or prove
all possible concurrent interleavings.

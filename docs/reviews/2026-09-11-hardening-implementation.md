# Runtime, processing and retention hardening — 2026-09-11

This implements the eight requested hardening areas on `dev`, with incremental
commits by `Patrik <patrik@local>`. The work was reviewed across database/recovery,
pipeline/concurrency and frontend/accessibility boundaries. No deployment or
remote publication is part of this change.

## Implementation

| Area | Implemented behavior | Evidence and limits |
| --- | --- | --- |
| Database privileges | Runtime DML credentials are separate from the non-administrative migration owner and recovery administrator. A one-shot migration service precedes API startup. Existing installations have an explicit offline ownership/credential cutover. | Disposable role, migration, backup, restore, quarantine and injected-rollback checks. Runtime credentials cannot perform DDL. See [database privileges](../pages/database-privileges.md). |
| Container constraints | All eleven services have CPU, memory, swap and PID ceilings, read-only roots and restricted writable paths. Runtime processes drop capabilities; PostgreSQL and Redis retain entrypoint capabilities only. | Tracked-source image builds and an owned Compose stack verify actual cgroup settings, non-root temporary writes, export CPU pressure, isolated OOM behavior and restart. The 16 GiB starting profile remains provisional. |
| Repair dispatch | Durable item/stage generations, claim tokens, expiry, bounded retries, global/per-feed admission and a rotating feed cursor govern repair. Queue-canary progress prevents repeated publication while consumers are paused. | PostgreSQL concurrency and generated crash sequences cover publication ambiguity, expiry, replacement claims, stopped runs and fair admission. |
| Database budgets | Finite pools and overflow limits apply per process; prefork children discard inherited pools. Short database units share a remaining-time budget through SQL, flush, progress updates and deferred precommit constraints. Final durability/network acknowledgement can still have an uncertain outcome. | Real pool/lock contention, deferred constraint execution and late commit tests verify rollback and sanitized retryable API failures. External actions keep their own deadlines and authorization fences. |
| Processing worklist | Operations exposes scoped items/stages, reason, age, attempts and retry time; selected retries create durable, idempotent, principal-owned runs with progress, cancellation and audit. URLs preserve navigation context. | Actual-session/cache tests and real-server browser workflows exercise acceptance, progress, cancellation, owner isolation and reload. Recovery does not directly replay AI/provider or integration side effects. Normal downstream processing remains possible. |
| Freshness and capacity | Classification, tagging and export backlog ages join health issues and history. Fixed-cardinality counters record deadline failures; sampled gauges record database waits and collector memory pressure. Telemetry delivery has bounded concurrency and health collection has a wall-clock limit. Mixed workloads include simultaneous durable exports over disjoint source sets. | Workload artifacts identify the source commit, limits, workload contract and hardware label. Comparison rejects incompatible or insufficient measurements. Memory telemetry covers the collector; intended-hardware qualification remains an operator task. |
| Incremental retention | Seven expired history families drain child records across bounded commits. Durable claims prevent late references and reactivation. Permission-bearing parents become unreadable before provenance is removed. | Large-parent, rollback, concurrent writer, source pin, dispatcher/final-delete and downgrade tests. Protected evidence and unresolved external-effect receipts remain protected. |
| Architecture and lifecycle contracts | Guards enforce route/composition boundaries and keep feed dependency declarations below orchestration. Thirty-two unused private dependency parameters were removed. Shared editor lifecycle tests cover asynchronous saves, selection, discard and retired sessions. | Static dependency checks include relative and literal dynamic imports; domain tests retain actual database and cache transitions. Large legacy services still merit gradual decomposition. |

## Rollout

Read [database privileges and upgrades](../pages/database-privileges.md) before
upgrading an existing volume. Keep the initialized administrator/database identity,
back up first, add distinct runtime and migration credentials, and complete the
offline cutover before restarting application services. Deploy the backend,
frontend and migrations `0091` through `0094` together, with old Beat stopped
while schedules change. The migration container must use the same backend image
as runtime services; the release verification workflow now pins it too.

Review the [resource and connection inventory](../pages/runtime-budgets.md) against
the actual host and replica count. These are finite ceilings rather than a
throughput guarantee. Increasing export limits also requires reviewing temporary
storage, database storage, memory and connection allocation.

Partially pruned permission history stays hidden after cancellation, restart or
a longer retention policy. Finish eligible cleanup before downgrading below
`0094`; the migration refuses a downgrade that would expose partial permission
provenance. See [incremental cleanup](../pages/incremental-retention.md).

## Integrated validation

Validation used synthetic disposable services and tracked-source image builds.
No existing deployment, configuration secrets or user data were used.

| Check | Result |
| --- | --- |
| Backend and coverage | Frozen `4cbb723`: 2,423 passed, two opt-in capacity cases skipped, one export lease test failed after its renewal assertions. Combined line/branch coverage was 84.82%, reporting 78.18%; all 38 critical-module floors passed. The test-only synchronization fix and its ordered predecessors then passed all 14 cases; application code was unchanged. |
| Frontend | 945 unit/DOM tests across 111 files; lint, TypeScript, production build and rendered bundle smoke passed. |
| Browser workflows | Nine new mocked workflows passed across Chromium, Firefox and WebKit. All 39 real-server workflows were verified: 38 passed together; one Chromium navigation interrupted by a Docker network change passed unchanged on rerun. |
| Migration compatibility | Populated `0042 → 0094`, identity/JSON preservation, downgrade to base, re-upgrade and both Alembic drift checks passed on PostgreSQL 16.14 at `4919321`. |
| Recovery roles | 87 ordinary recovery checks and three Docker end-to-end cases passed. Tracked image `9fd091b` exercised split-role upgrades, restore/quarantine, injected rollback, partially pruned permission history, and `0094 → 0090 → 0094`. |
| Runtime isolation | The owned eleven-service stack at `8f1d53e` passed actual cgroup/writable-path inspection, CPU-pressure readiness, an isolated OOM probe, and export/Beat restart. Eight readiness probes under CPU pressure stayed below 2.04 seconds, including the existing two-second worker inspection. |
| API and source gates | API generation produced no contract diff. Source-size and Ruff checks passed for 687 production files; aggregate whitespace and changed-document file links passed. |
| Capacity completion contract | Nine PostgreSQL cases prove an empty broker cannot hide stale classification, missing/blank content, incomplete fetches, IOC work or pending tagging. Diagnostics exclude article bodies. |
| Final capacity profiles | Smoke, 600-second sustained load, large disjoint exports and real prefork/Redis crash recovery all passed at immutable `6823cd6`, with no configured budget violations. See the [measurements and conditions](capacity/2026-09-11-hardening-summary.md). |

The backend application was frozen at `4cbb723`; `6823cd6` strengthens only
capacity tests and their documentation. The later lease test uses a controlled
worker clock and observes a real committed renewal before checking ownership past
the original expiry. Its original failure reported a failed job at final completion;
the swallowed generation exception was not available, so this is not attributed
to a demonstrated application defect. Future failures include the durable error
code. The full run was not repeated after this test-only change. Together with
the nine added capacity predicate cases, 2,433 ordinary backend cases were
verified across the full run and focused checks. Frontend and container checks
are recorded at their tested revisions; later changes received targeted
validation, including 11 Processing workspace DOM cases after the copy update. These are local checks; no remote
GitHub CI run or publication is claimed. Earlier incomplete or mutable-source
capacity probes are not release-comparison evidence.

## Remaining qualification

| Area | Remaining work |
| --- | --- |
| Deployment sizing | Run sustained mixed load, disjoint large exports and recovery on the intended hardware with realistic catalog sizes and external dependency latency. No target hardware or production workload was supplied. |
| Deployment variants | The role split and recovery adapter qualify the bundled Compose topology. External PostgreSQL, custom roles/schemas, database proxies and additional data accessors need their own adapter and upgrade/recovery qualification. |
| Fleet observability | Collector cgroup measurements do not represent every replica or the host. Use host/container monitoring for fleet memory, disk and network pressure, and retain capacity artifacts across releases. |
| Retention coverage | Unresolved external-effect receipts, retained evidence and unsupported oversized dependency bundles stay protected. Extend pruning to additional history families only with their reference and authorization invariants covered. |
| Accessibility and identity environments | Automated browser/axe checks do not replace manual screen-reader testing or qualification against an organization's identity provider and proxy configuration. |
| Architecture at larger scale | The selected boundaries are enforced, but they are not a complete dependency graph. Continue gradual service decomposition and measure large-catalog aggregate queries before increasing limits. |

See the [processing recovery guide](../pages/processing-recovery.md),
[freshness history](../pages/processing-freshness.md),
[capacity harness](../reference/capacity-baseline.md), and
[lifecycle test contracts](../development/lifecycle-contracts.md).

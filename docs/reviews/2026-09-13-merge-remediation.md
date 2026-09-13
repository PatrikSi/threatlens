# Main merge remediation — 2026-09-13

The findings in the [merge-readiness review](2026-09-13-main-merge-readiness.md)
are corrected. Independent review and integrated validation also identified and
corrected an investigation permission-expiry gap, dialog focus loss, stale
browser assumptions and incomplete checked-in dependency notices.

The application is prepared as **2.0.0** on `dev`. See the
[release notes](../releases/2.0.0.md) for the database-role cutover, coordinated
worker upgrade and new report publication defaults. Merge qualification requires
the final commit's complete [quality workflow](../../.github/workflows/quality-gates.yml)
to pass, including both application architectures. Publishing continues to
require scans and smoke checks of the exact promoted image digests.

## Corrections

| Area | Result | Regression / evidence |
| --- | --- | --- |
| MR01 · Team authorization | Investigation creation and canonical team access share a post-lock membership check. | A real PostgreSQL test waits for a team lock until OIDC membership expires; the old implementation fails the regression. |
| MR02 · Report governance | Schedule writes serialize the exact submitted `review_required` value. | Browser/DOM tests exercise both directions and reopening. Three API tests also verify database persistence, default behavior, omitted legacy fields and stale-version rejection. |
| MR03 · HTTP deployments | Exports, recovery, AI/report requests and legacy dashboard-window parsing share secure UUID generation. Preparation errors preserve user state and explain failure. | Native nonsecure HTTP application workflows run across Chromium, Firefox and WebKit. Tests cover UUID format, unavailable entropy, ambiguity retries, closing/reopening recovery and new keys after success. |
| MR04 · Backend image | The OS layer explicitly upgrades PCRE2 and refuses versions below `10.42-1+deb12u1`. | A freshly pulled base still contained the vulnerable version. Rebuilt native images pass the fixable HIGH/CRITICAL scan; the actual native library, dependencies and PDF generation work. |
| MR05 · AI freshness | Latest-finished statistics exclude unfinished runs and retain permission filtering. | Completed/queued combinations, empty data and inaccessible newer results are covered. |
| Investigation write expiry | All ten mutation routes preserve the accepted credential's permission cap and recheck current write grants and team membership before commit. The checkpoint adds no locks. | Fourteen regressions include real HTTP requests waiting on team, investigation and note rows while either required write permission expires. Failures roll back; valid and restricted-credential controls are included. |
| Dialog lifecycle | One dialog layer remains registered through pending/error changes to its initial-focus reference. | Focus returns to the opener after failed submission; nested session verification retains correct stack order. |
| Test lifecycle | Provider statistics tests follow current navigation. Real-server routing tests await the exact successful PUT before independent persistence checks. Recovery version-mismatch fixtures stay distinct from the application release. | The original WebKit trace showed its GET beginning before the PUT finished. Corrected workflows pass across all three engines. Fresh CI identified the version fixture's collision with 2.0.0. |
| Alert previews | Entity decoding processes the original text once, preserving nested escaping. | Three new tests cover adjacent, nested, unsupported and invalid entities; all eight alert-model tests and frontend lint pass. The original display defect did not execute HTML. |
| AI statistics coverage | The enabled-provider CI step includes the real statistics authorization/accessibility case in each browser. | Earlier CI skipped this case because its title did not match the provider-settings filter. The local enabled-provider suite already exercised it; future CI includes all 18 real-server cases per engine. |
| Retention transaction budgets | Materialize the bounded, locked child selection before deletion in both history and permission-history pruning. | Full CI reproduced a call deleting 25 expired receipts with a budget of seven. A valid PostgreSQL nested-loop plan re-evaluated the limited selection. Regressions exercise that plan and composite-key children while preserving eligibility and lock safeguards. |
| Release artifacts | Frontend runtime inventories and legal notices match the qualified image. | 114 package records and 114 legal-file hashes were verified. A new image-artifact gate checks both native images and rejects deliberately stale reference data. |
| Pre-merge platform coverage | CI builds, scans and starts amd64 and arm64 application stacks; PostgreSQL and Redis remain native CI infrastructure. | ARM64 uses the existing QEMU action and publication's emulated worker-health budgets. Actionlint passes. Native smoke retains normal deployment health budgets. |

Commits use `Patrik <patrik@local>` and imperative messages. Core fixes are
`ad55f53`, `54f1b23`, `1957013`, `b43e436`, `46496aa`, `0c63f75` and `09485b4`.
Version/upgrade metadata is in `11f718b`; additional persistence, browser,
artifact and CI corrections follow in separate commits.

## Integrated validation

- **Backend:** the complete run at `11f718b` passed **3,203 tests**, with two
  opt-in workload skips. Coverage gates passed at **85.87% overall** and
  **86.61% reporting**. The subsequent investigation authorization correction
  passed **51 tests** across the affected API/access suites, including its
  fourteen new regressions. The three schedule API persistence cases passed
  separately. Per-commit CI runs the complete combined suite.
- **Frontend:** **1,167 tests across 134 files** passed on Node 22.23.2, followed
  by lint, production build, login-bundle smoke and dependency audit.
- **Interaction browsers:** **63 of 66** passed initially. The remaining three
  instances of the stale provider-statistics test passed after correction, one
  per engine. The new HTTP-origin and dialog regressions were included.
- **Real-server browsers:** **53 of 54** passed initially. The provider-routing
  synchronization fix subsequently passed in all three browsers. One Chromium
  rerun was needed after concurrent disposable Docker network changes caused
  `ERR_NETWORK_CHANGED`; the test passed after network activity settled.
  These counts combine integrated runs with targeted reruns. Browser fixtures
  use real PostgreSQL, sessions, CSRF, OIDC and application handlers; external
  providers remain controlled synthetic services.
- **Native images and running stack:** committed runtime source `09485b4`,
  version `2.0.0`, passed 30 real HTTP/API/authentication checks, migration
  `0105`, all service isolation/provenance checks, proxy upload limits, normal
  and upstream-failure log privacy, and API restart. Ten long-running services
  were healthy and the migration service exited successfully.
- **Recovery:** all four disposable Docker recovery tests passed in 193.7
  seconds, including populated backup/drill/destructive restore, enterprise
  evidence preservation, role-fence rollback, legacy ownership/sequence
  cutover and migration-owner/runtime privilege checks. The separate ordinary
  recovery suite ran 91 tests successfully with four opt-in Docker skips;
  recovery shell syntax and ShellCheck passed.
- **Security/artifacts:** Python and JavaScript dependency audits reported no
  known vulnerabilities. Both native images reported zero fixable HIGH/CRITICAL
  findings. The tracked-source secret scan reported no findings. Actual image
  inventories and bundled legal files match the refreshed references; a
  deliberately stale reference is rejected. Generated API/preview artifacts,
  source-size, Ruff, compilation, Actionlint and aggregate diff hygiene passed.

The first remote run at `f81f21f` passed all three browser jobs (117 executed
cases), frontend tests/build/audit, migrations and both CodeQL analysis jobs.
Its recovery failure exposed the release-number collision described above;
after correction the complete ordinary recovery suite passed again (91 tests,
four opt-in skips). The [CodeQL triage record](2026-09-13-codeql-triage.md)
assesses all 24 findings from that commit and links the subsequent display fix.
The final workflow must also cover the added statistics cases and both image
architectures; earlier green jobs do not substitute for final-commit results.

At `8289d8b`, the complete remote run passed 1,170 frontend tests and 120 browser
executions without retries, all four disposable recovery drills, ordinary
recovery tests, migrations and both CodeQL jobs. Its backend run passed 3,219
tests with two opt-in skips and one retention-budget failure. That failure was
reproduced with the production helper and an alternative valid PostgreSQL query
plan, leading to the materialized-selection correction above. Image jobs were
correctly withheld after the backend failure; this run alone is not merge
qualification.

The retention correction is committed in `b1a3ae5`. Both deterministic
regressions fail on the old implementation (25 deleted instead of seven), and
all 39 affected history/pruning tests pass after the correction. Coverage is
94% for history maintenance, 93% for permission-history pruning and 96% for
history pruning. An independent scan and forced-plan PostgreSQL probes found
no additional affected deletion/claim query; other paths already materialize
their locked selection or use bounded nonlocking selections.

Local native image IDs:

- Backend: `sha256:5cbb96aff9ae5dbe72a8af266e4e38ab4aef8d3e3e61f8a2de2aa9ad4a55d491`
- Web: `sha256:120ff6939a34a2d35f2e610c63710713d2c81bd3d5b6ceea36691e6295ead8f4`

These are local qualification images, not published release digests. ARM64
build/execution attempts on this host failed because emulation is absent. No
host emulator was installed; the pre-merge CI matrix provides that qualification
on disposable runners. Test/image services were isolated from the live stack,
environment secrets and existing backups.

## Capacity and recovery measurements

Both committed refs were measured for 600 seconds with the same constrained
service workload on `patrik-local-shared-20260913`:

- [Baseline `dafe832`](capacity/2026-09-13-merge-baseline-sustained.json)
- [Candidate `09485b4`](capacity/2026-09-13-merge-candidate-sustained.json)
- [Compatible comparison](capacity/2026-09-13-merge-capacity-comparison.json)

Both passed all workload budgets. The comparison had enough required samples
and flagged no regression. The candidate completed 480 ingested articles,
295 successful exports, 301 successful AI connection-test operations and 300 governance
updates. Six export policy conflicts were recorded as safe rejections. Export
success P95 was 434 ms, AI connection-test success P95 276 ms, peak process RSS 331 MiB and
sampled oldest pending-message age 3.12 seconds. Both disjoint export workers
completed during and after the candidate load.

The [large profile](capacity/2026-09-13-merge-candidate-large.json) also passed:
200 new articles, two concurrent completed exports of 1,000 retained articles
and 66,438,780 bytes each after the mixed load, and approximately 144 MiB
process RSS growth. During the policy-update phase both asynchronous exports
were safely rejected for policy conflicts. Backlog recovery, including the
consumer outage/startup, was 72 seconds within its 240-second budget.

The separate [crash-recovery profile](capacity/2026-09-13-merge-candidate-recovery.json)
passed, preserving accepted Redis work through broker restart and recovering
processing after killing the owned worker child. Later measurement refs differ
only by test/documentation/CI changes from the qualified runtime source.

These are bounded synthetic service measurements on a shared host, not a
deployment sizing guarantee. Background-export latency has too few samples for
a trend conclusion; the comparison discloses this. Concurrent host activity also
means lower observed timings should not be attributed to the fixes. The
[capacity runbook](../reference/capacity-baseline.md) describes the measured
scope, resource limits and interpretation.

## Remaining operational choices

No additional confirmed application blocker remains in the reviewed scope.
Human screen-reader evaluation, real-provider quality evaluation and dedicated
production sizing remain separate qualifications. Teams supplement feature and
evidence permissions rather than providing complete global tenant isolation.
Keep the documented major-version cutover, current CI results and exact-image
publication gates as release requirements.

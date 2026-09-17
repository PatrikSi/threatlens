# September 17 review remediation

This follow-up implements the thirteen findings in the export, MCP, report
review, hunt authoring, and statistics review. It preserves the existing
authentication model and adds no database migration.

## Findings and changes

| Finding | Implemented behavior | Regression coverage |
| --- | --- | --- |
| REV01 · Export capacity | Background export downloads reserve real temporary-file storage across API processes before decryption, retain the reservation through transfer, and return a controlled capacity error with retry guidance when space or admission is unavailable. | Allocation failure, concurrent processes, cleanup, interrupted transfer, and retained headroom. |
| REV02 · Export preparation | A single background-download preparation deadline covers database work, decryption, writes, audit, and the final authority check. A timed-out preparation releases its resources and leaves the durable export available for retry. | Expiry at preparation checkpoints, after database-budget restoration, audit failures, integrity failures, and transfer authorization lifetime. |
| REV03 · MCP query overhead | Authorization is reconstructed once under retained locks and reused only within the same request and transaction. Current credential, owner, policy, resource, and clock checks remain enforced. Timeout settings change only when the effective budget changes. | Request-local query budget, expiry, revocation, policy changes, lost transactions, and repeated independent reads. |
| REV04 · Report qualifications | MCP report responses include bounded stored coverage and grounding qualifications. Oversized or non-object legacy metadata produces an explicit incomplete-details warning. | REST/MCP consistency for checked, degraded, insufficient-evidence, and human-edited reports; oversized metadata. |
| REV05 · Hunt handoff | Creating an investigation from an accepted hunt writes the complete reviewed snapshot as numbered notes in the same transaction. Passages and analyst review precede the longer context lists; valid long cards no longer lose trailing fields. | Short and long cards, complete multi-note handoff, repeated requests, and retained assessment history. |
| REV06 · Hunt draft reconciliation | Each hunt draft retains its immutable baseline. Saving one hunt rebases other unchanged hunts safely; subsequent edits and changed underlying evidence remain distinguishable. | Sibling saves, concurrent edits, changed hunts, access changes, and recovery. |
| REV07 · Session draft protection | Session-level unload protection survives article collapse and navigation. A recovery dialog offers resume and deliberate discard, rechecks access, and preserves copyable notes when write access alone is lost. | Navigation/collapse, delayed saves, session isolation, transient errors, and read/write permission changes. |
| REV08 · Retained report evidence | A permission-checked endpoint selects bounded source passages in SQL. The evidence dialog pages through the retained text while pinning both report and source revisions. | Authorization, bounded SQL, Unicode offsets, revision conflicts, unavailable legacy passages, paging, errors, focus restoration, and accessibility. |
| REV09 · MCP failure headers | The outer transport consistently applies allowed-origin CORS, no-store, and correlation headers, including admission and deadline failures. | Early rejection, saturation, timeout, preflight, and disallowed origins. |
| REV10 · MCP cleanup failures | Rollback and invalidation failures are isolated so the original JSON-RPC error contract survives database cleanup failure. | Both cleanup failure paths and retained original errors. |
| REV11 · Heatmap accessibility | The ingestion heatmap has a keyboard bucket selector with exact UTC values and a paginated accessible data table. | Keyboard interaction, exact values, pagination, mobile layout, access errors, and automated accessibility checks. |
| REV12 · MCP outcome logging | The outer boundary records correlated admission/preparation and final delivery/cleanup outcomes. A prepared response is distinct from a completed transfer. | Early failures, interrupted delivery, cleanup failures, and correlation; positive assertions use real captured logs. |
| REV13 · MCP database errors | PostgreSQL statement cancellation maps to `504/mcp_deadline`; lock, deadlock, and serialization contention map to retryable `503/mcp_database_busy`. | Explicit database error classification and safe diagnostic responses. |

The comparable measured MCP article read decreased from **160 to 42–43 database
driver calls**. The latest warmed read comprised 38 application SQL statements,
three timeout-setting calls, and two pool pre-pings. Timeout-setting calls vary
with the remaining request budget; the regression test counts application and
timeout SQL separately from pool pre-pings.

## Validation

- Backend: the clean full run passed **3,693 tests**, with five expected opt-in
  capacity/SDK skips, in 16 minutes 17 seconds. A subsequent focused run passed
  all **nine retained-evidence tests**, including two added checks for missing
  citations and the distinction between an exact end offset and an invalid one.
  Combined line/branch coverage is **86.43% overall** and **86.74% for reporting**.
  Every critical-module floor passes, including five new helper-module gates;
  the retained-evidence reader has 100% coverage. No existing floor was lowered.
- Frontend: **1,197 tests in 140 files passed**, including asynchronous draft,
  evidence-dialog, and permission-refresh cases. TypeScript, ESLint, production
  build, and the production-bundle smoke passed.
- Browser workflows: **12 cases passed across Chromium, Firefox, and WebKit**,
  covering hunt drafts, team context, heatmap accessibility, and retained report
  evidence. These use controlled local API fixtures and automated accessibility
  checks; they do not substitute for testing with assistive technology users.
- MCP SDK qualification: **119 protocol/SDK cases and two authenticated HTTP
  cases passed**, including both supported protocol revisions. The optional SDK
  dependencies were installed in an isolated environment; `pip check` passed.
- Real proxy qualification: current source passed discovery, scoped retrieval,
  both SDK modes, allowed-origin CORS, and credential revocation through
  disposable **nginx and Uvicorn**. All temporary containers and networks were
  removed after the run; the running application was not restarted.
- Deployment configuration: **23 bootstrap, Compose, and Kubernetes tests
  passed**. Both export preparation settings are represented in the shared
  environment inventory and configuration documentation.
- Capacity smoke: passed with no budget violations. Consumer recovery took
  approximately **5.5 seconds**, sampled lock-wait query age peaked at **27 ms**,
  and application RSS increased by approximately **40 MiB**. These are local
  smoke measurements, not sustained-load qualification for deployment hardware.
- Download storage: a disposable, constrained container with a real **512 MiB
  tmpfs** reserved a 250,000,000-byte anonymous file while preserving 64 MiB
  headroom. A second reservation returned the capacity error without allocating
  additional blocks; closing the first file allowed the next reservation and
  ultimately restored all free space. Container memory peaked at approximately
  251 MiB. The separate subprocess regression covers admission-lock serialization.
- Static checks: backend compilation, Ruff across application and test code,
  source-size checks for 846 production files, generated API-contract consistency,
  and diff hygiene passed. The final cleanup also removed 17 pre-existing test
  lint findings; all 28 tests in those affected modules passed.

Independent reviewers covered export lifetime and cleanup, MCP authority and
error contracts, draft lifecycle and access changes, retained evidence, and
keyboard/browser behavior. The broader suite also caught outdated download
fixtures and route-manifest expectations, which were updated to assert the new
contracts rather than bypassing their checks.

In-process Alembic setup also exposed test-order leakage: its CLI logging
configuration disabled application loggers and replaced pytest's capture
handlers. Test setup now preserves the existing handlers only for this
repository's migration configuration; production migrations still run in their
separate process. The reordered migration-to-MCP sequence passed all 40 cases
with its positive logging assertions intact.

## Operating details and limits

- `EXPORT_DOWNLOAD_PREPARATION_TIMEOUT_SECONDS` defaults to 30 seconds.
  `EXPORT_DOWNLOAD_SCRATCH_HEADROOM_BYTES` defaults to 64 MiB. Download admission
  requires the supported Linux filesystem allocation semantics and a shared
  temporary directory for processes sharing the storage budget.
- The preparation deadline checks application work and bounds database
  operations. It cannot preempt a blocked kernel filesystem call; initial
  connection establishment and cleanup also remain subject to their ordinary
  driver/operating-system limits.
- Hunt drafts are session-memory state. Save persists the review; the unload
  guard and recovery dialog protect unsaved work but do not make it durable
  across reload or sign-out. Investigation handoffs remain ordinary auditable
  notes, with all numbered parts retained.
- The report evidence endpoint is additive. Its JSON response contains retained
  plain-text passages, and later pages require the first page's source revision. A
  changed report/source produces a refreshable conflict instead of combining
  passages from different revisions.
- No production dependency, database schema migration, or OAuth capability was
  added. The existing MCP client and operational limits in the
  [MCP implementation review](2026-09-17-mcp.md) still apply.

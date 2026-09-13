# Code review remediation — 2026-09-08

This records implementation of R01–R21 from the [original review](2026-09-08-code-review.md)
on `dev`, starting from `b56e707`. Changes are committed as `Patrik <patrik@local>`.
The original review remains a historical record of the defects before these fixes.

The table maps each original finding to the resulting behavior and its
verification. The final section records improvements outside R01–R21 and the
limits of the validation.

## Implemented changes

| ID | Area | Result | Principal verification |
| --- | --- | --- | --- |
| R01 | Webhook secrets | Delivery history uses configuration secret-read permissions; restricted readers receive no configured URL/header/query values or free-form diagnostics. | Actual role/token API combinations and serializer regressions. |
| R02 | Session isolation | Each session has its own QueryClient; request/mutation leases reject stale results. Silent identity changes are fenced before publication; file reads and request batches retain their originating lease. | Real-provider and QueryClient tests, including a reproduced silent-identity publication race. |
| R03 | Dialog reliability | One dialog stack owns background isolation, topmost Escape handling, and focus restoration. | Nested close/unmount DOM tests and Chromium Back/discard workflow. |
| R04 | Verification outages | Cached workspaces and drafts remain mounted behind a blocking recovery dialog; authenticated writes pause, and confirmed expiry removes protected content. | Transient failure, recovery, 401, and cross-tab browser flows. |
| R05 | Report scheduling | Skip-missed policy permits normal ticks through an explicit five-minute dispatch grace; existing retries retain their original occurrence. Latest-only periods use scheduled time. | On-time, exact-grace, late, backlog, forced, and retry timing boundaries. |
| R06 | Outbound deadlines | Total monotonic budgets cover DNS, connections, TLS, partial headers/writes, redirects, body reads, and domain waits. HTTP work stops synchronously before task/policy fences release. | Slow real sockets, DNS saturation, redirects, blocked writes, and actual policy/task lock tests. |
| R07 | Classification recovery | Source changes and required revisions commit together. Classification acknowledges under the item lock; repair finds missing and stale work. Migration 0086 reconciles historical hashes. | Failed publication/recovery, concurrent writers, rollback, lifecycle, and populated migration cases. |
| R08 | Schedule concurrency | Reservations recheck retry eligibility/version under the row lock; failure settlement is conditional on the attempted version and tick. | Overlapping PostgreSQL sessions and dispatcher regressions. |
| R09 | AI response memory | Success and error responses stream through encoded and decoded caps, defaulting to 2,000,000 bytes. Ambiguous attempts remain non-retryable. | Compressed, chunked, oversized, incomplete, and provider-receipt tests. |
| R10 | Outbound address policy | Default destination policy rejects non-global unicast, including shared address space and mapped representations. | Literal, DNS, redirect, and outbound-surface tests. |
| R11 | Export timeouts | nginx allows 310 seconds for export response data, aligned with the browser's five-minute request budget. | Isolated nginx returned HTTP 200 after a 65.06-second upstream delay. |
| R12 | Export/report memory | Database payload batches use an estimated 8 MiB ceiling; unused fields are omitted, association growth is fenced in materializing queries, and report plans retain bounded excerpts under a 32 MiB candidate budget. | Real large-body/IOC/metadata rows, between-query growth, all export formats, report excerpts, and permission tests. |
| R13 | AI analytics | Permission-aware SQL aggregates and percentiles replace raw usage/enrichment/task materialization in the overview. UTC buckets and null/percentile behavior are preserved. | Cardinality probes, percentile ties, nulls, non-UTC sessions, and handling-policy regressions. |
| R14 | Report authoring | Template refreshes preserve drafts and loaded revisions; deliberate reload/selection confirms unsaved changes. Generation responses preserve later edits. | Builder lifecycle and report-controller DOM regressions. |
| R15 | Pending saves | Feed, SMTP, and webhook editors reconcile against submitted fields; AI configuration disables editing while saving. Entity changes fence local completion. | Late-save real QueryClient tests and Chromium pending-feed-save workflow. |
| R16 | Report library | All accessible reports can be paged, with visible ranges and status/UTC creation-date filters. | Pagination/filter DOM cases and API range/permission tests. |
| R17 | Dashboard access | Arrow keys move/resize panels, Shift accelerates, and focus activates panels. Editing waits for configuration hydration. | Geometry/focus DOM cases and keyboard-only Chromium workflow. |
| R18 | Alert context | Views, filters, pages, selected occurrences, and activity pages live in URLs; copy links support manual fallback. | Reload, deep-link, Back/Forward, and router/QueryClient cases. |
| R19 | Preview privacy | External browser resources require explicit per-article consent; scripts, forms, and frames remain blocked. | Sanitizer/CSP tests and actual Chromium blocked/opt-in/reset requests. |
| R20 | Test strategy | Real asynchronous cache/draft transitions and seven critical Chromium workflows now run in quality gates with retained failure traces. | Integrated frontend and browser quality gates. |
| R21 | Capacity evidence | A disposable Celery/PostgreSQL/Redis workload measures concurrent ingestion, exports, governance, AI, queue recovery, memory, and lock-wait samples against provisional budgets. CI runs its smoke profile. | [Recorded integrated baseline](../reference/capacity-baseline.md): 60 articles recovered in 17.83 s, export p95 711 ms, RSS increase 66.1 MiB, no violated budgets. |

## Cross-review

Separate agents implemented client foundations, client workflows, and network/
pipeline changes. They then reviewed each other's work and the root changes.
That second pass found and fixed silent account changes before cache rotation,
session continuation after file reads, a pending AI configuration overwrite,
an unused large IOC field, growth between export association queries, and an
incorrect omitted-text PDF notice. The final transport pass reproduced and
fixed unbounded OIDC/webhook decompression. Scheduler cross-review added actual
PostgreSQL transaction/publication tests and retained the extracted module's
coverage floor. No further blocker was found within those reviewed paths.

## Validation

- Integrated backend: 2,068 tests passed in 16 minutes 26 seconds; the opt-in
  capacity module was skipped here and run separately. Branch-inclusive coverage
  was 83.76% overall and 77.76% for reporting, passing the overall and critical
  module floors. The extracted schedule dispatcher reached 100% line/branch coverage.
- Frontend: 897 unit/DOM tests, lint, TypeScript/Vite build, production-bundle
  smoke, and seven Chromium workflows passed. The final browser run used the
  pinned Playwright package's matching Chromium 153 with networking disabled.
- PostgreSQL: populated 0042 upgrade through 0086, compatibility verification,
  Alembic schema check, full downgrade, and re-upgrade passed in a disposable database.
- Recovery tooling: 84 non-destructive tests passed; the opt-in Docker test was
  also run separately and passed in 124 seconds against the new backend image.
  It exercised backup, verification, drill, restore, quarantine, and ledger
  reconciliation using disposable databases. Shell syntax and ShellCheck passed.
- Python runtime audit and npm audit: no known vulnerabilities at validation time.
  Two development-tool advisories were patched: [humanfs](https://github.com/advisories/GHSA-p498-v437-472g)
  and [postcss-selector-parser](https://github.com/advisories/GHSA-w9m9-85wc-3x92).
- Both custom amd64 images built from a clean tracked checkout with refreshed
  base images. Trivy reported zero fixable High/Critical vulnerabilities in
  either image. This does not cover unfixed advisories or an arm64 rebuild.
- Isolated full-stack smoke passed with the built image pair: nginx, API,
  PostgreSQL, Redis, all worker queues, and Beat reached aggregate readiness.
  The probe verified published OpenAPI, real cookie login and protected reads,
  migration 0086 at head, and the 310-second export proxy configuration. Both
  test networks blocked external connectivity.
- API reference and OpenAPI regenerated; browser preview fixture matches the backend.
- The Python runtime lockfile matches a fresh constrained dependency resolution.
- Capacity smoke and integrated baseline passed. The baseline records expected
  policy conflicts separately from successes, with no unexpected task/sampler
  errors or budget violations. See the [runbook and recorded measurements](../reference/capacity-baseline.md).
- Source-size gate passed for 627 production files; backend Ruff checks passed.

These checks use disposable services and deterministic providers. Browser tests
exercise the real frontend with intercepted APIs. They do not establish live
OIDC interoperability, every assistive-technology/browser combination, or
production throughput. No application production data was used.
These are local checks; this `dev` implementation has not been pushed or run by
GitHub Actions yet.

## Deployment

Migration `0086_classification_versions` scans retained source text to reconcile
historical classification hashes. Stop ingestion/classification writers for the
cutover and replace workers together; see [cutover details](../reference/pipeline.md#classification-recovery-cutover).
Measure migration time on a representative copy for a large installation.

New environment settings are `FEED_TOTAL_TIMEOUT_SECONDS` (60),
`ARTICLE_TOTAL_TIMEOUT_SECONDS` (90), `AI_RESPONSE_MAX_BYTES` (2,000,000), and
`OIDC_TOTAL_TIMEOUT_SECONDS` (30). They are wired into the shared Compose backend
environment and documented in the [configuration reference](../reference/configuration.md).
Additional proxies must honor the [export timeout window](../pages/export.md).

## Remaining assessment

This section records the earlier checkpoint. Its seven follow-up priorities
are addressed in the [follow-up implementation and current assessment](2026-09-08-follow-up.md),
which contains the later verification and remaining deployment limits.

The modular monolith remains a suitable architecture for a shared self-hosted
installation. Bounded I/O, durable processing intent, database aggregation, and
session-scoped client state address the immediate cross-cutting defects without
requiring a service split. Remaining priorities are focused on execution limits,
measured scale, and broader operational validation.

| Priority | Area | Remaining improvement | Evidence or limit |
| --- | --- | --- | --- |
| P1 | Custom rule execution | Add bounded-time regex evaluation or a safe engine, with explicit invalid/timeout feedback. | Custom patterns still reach Python `re.compile`/`search` in `algorithm_tags.py`; HTTP deadlines do not bound CPU regex work. This is separate from R01–R21. |
| P2 | Failure analytics | Aggregate or page the separate AI failure-history query before loading ORM rows. | R13 fixes the overview; `list_ai_failures` still scans/materializes matching error events and runs in Python. |
| P2 | Production capacity | Run larger corpora, skewed sources, sustained load, and actual deployment/process failure drills on target hardware. | The reproducible small workload is a regression baseline, not a supported-throughput guarantee. |
| P2 | Long exports | Introduce durable asynchronous export jobs if required workloads exceed five minutes; tune PDF and concurrent-process memory from measurements. | Preparation remains synchronous; payload caps do not equal exact RSS ceilings. |
| P2 | End-to-end assurance | Add real-server browser flows with cookie/CSRF/OIDC, provider failures, and automated accessibility plus screen-reader checks. | Current browser cases use deterministic intercepted APIs and Chromium. |
| P3 | Report navigation | Consider keyset pagination and richer report search when the retained library warrants it. | Offset pagination is complete but a concurrently changing library can shift pages. |
| P3 | Operations | Trend workload results, queue age, deadline failures, and lock-wait budgets in release decisions. | Per-run measurements need repeated hardware-comparable baselines before setting production SLOs. |

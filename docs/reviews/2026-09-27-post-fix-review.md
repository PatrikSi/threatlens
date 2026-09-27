# Comprehensive post-fix review — 2026-09-27

Reviewed source revision: `5e847a8` on `main`. Three independent reviewers
covered resilience, security and UI/UX, with a coordinating pass over code,
deployment, monitoring, recovery and validation. This review adds evidence and
findings; it does not implement the newly identified behavior changes.

The requested capabilities are substantially present, and the preceding
RV01–RV15 corrections have regression coverage. There are still **nine P2 and
two P3 confirmed findings**. Prioritize AI recovery and the missing UI lifecycle
contracts before adding another major feature. No new authentication bypass,
cross-team read, arbitrary code execution or default-policy SSRF defect was
demonstrated in the inspected paths. This is a scoped result, not a security
certification or a claim that every possible state was exercised.

## Confirmed findings

Source line references below apply to the reviewed revision.

| ID | Priority / area | Finding and impact | Recommended correction |
|---|---|---|---|
| NR01 | P2 · AI recovery | Automatic repair creates a fresh extraction task after an ordinary terminal failure, repeating completed paid sections and replacing the accepted progress/budget. | Resume the exact accepted plan and checkpoints; respect receipt retryability and require deliberate authorization for a fresh plan. |
| NR02 | P2 · AI resilience | A synthesis failure prevents publication of completed extraction; complete section coverage then makes continuation return 409. | Publish verified extraction with a disclosed synthesis limitation and support receipt-safe synthesis-only recovery. |
| NR03 | P2 · Report integrity | Image alt text is visible in reports/exports but absent from citation validation. An uncited numerical claim passes. | Reject images or validate their displayed descriptions and require navigable attribution; share cases across validation and rendering. |
| NR04 | P2 · Hunt authoring | Filtering a dirty hunt schedule out of the queue silently discards its priority/deadline. | Preserve keyed drafts or coordinate discard confirmation across filtering, pagination, navigation and polling changes. |
| NR05 | P2 · Access-state UX | Operations retains previously authorized metrics after 403 and hides the denial reason. No new unauthorized response was demonstrated. | Distinguish explicit access loss from transient failure for every Operations dataset and action. |
| NR06 | P2 · Mobile triage | An ordinary read article in compact view can have an unnamed, zero-height detail button. | Provide an always-visible, named internal-detail action independent of status badges. |
| NR07 | P2 · Keyboard accessibility | The full-screen mobile article inspector leaves focus on obscured background controls. | Use the shared dialog lifecycle or an actual detail route, including focus transfer, background isolation and focus return. |
| NR08 | P2 · Responsive operations | Trend sections grow beyond the mobile viewport and are clipped, including controls and data. | Constrain grid minimum sizing and keep overflow within accessible chart/table wrappers. |
| NR09 | P2 · Export isolation | Scratch cleanup for one database deletes active files belonging to another database sharing the temporary root. | Namespace scratch by installation/database and isolate test temporary roots. Supplied Compose tmpfs mitigates cross-container impact. |
| NR10 | P3 · Accessibility | Mobile usage records apply `aria-label` to generic divs, producing invalid ARIA. | Use appropriate labelled semantics or remove redundant labels; audit whole responsive pages. |
| NR11 | P3 · Monitoring | A single restart incident clears after one 60-second observation, before the bundled Prometheus alert's two-minute pending period. | Add a restart-event alert/counter or an intentional observation window that can reach firing state. |

### NR01 — automatic repair must preserve the logical operation

`backend/app/tasks/feed_task_dispatchers.py:207–230` selects recent failed
enrichments without interpreting existing section progress or receipt
retryability. `backend/app/tasks/feed_tasks.py:355–361` queues a fresh task, and
`backend/app/services/ai_extraction_sections.py:224–235` initializes new progress
when there is neither the same run ID nor explicit continuation authority.

A disposable PostgreSQL probe exercised real enrichment execution code, failure
finalization, the repair dispatcher, task creation and provider receipts in
process; it did not traverse a Celery broker. Provider transport was synthetic.
Section calls were **`[0, 1, 0, 1, 2]`**: section 0 succeeded,
section 1 had a nonretryable response-received failure, and automatic repair
repeated section 0. Receipts contained four successes and one failure. Source
tracing shows that the same reset branch can also replace an enlarged
continuation with the default eight-section plan; that expanded-plan scenario
was not separately exercised dynamically.

This is autonomous recovery, not deliberate user reprocessing. Existing global
quota and recent-item windows still apply; ambiguous provider receipts still
block unsafe I/O. The defect is repetition of received/terminal work and loss of
the logical plan, contrary to `docs/pages/ai-quality-and-coverage.md:42–62`.
Regression tests should exercise the sweep-to-worker path, including a failed
explicit continuation, changed source/provider settings and revoked authority.
Assert that accepted section/token allocations and completed receipts survive,
and that retries obey the existing attempt/receipt policy.

### NR02 — completed extraction needs an independent synthesis outcome

`backend/app/services/ai_extraction_sections.py:299–301` lets optional synthesis
failure abort publication before reaching the section-summary fallback at
lines 315–320. `backend/app/api/routes/ai_article_continuation.py:71–72` rejects
fully covered progress before considering synthesis recovery.

A real DB/API probe completed all three sections and simulated a proven-not-sent
synthesis failure. The enrichment ended in error, `structured_extraction_json`
remained null, and continuation returned 409 because uncovered characters were
zero. This also happens with automatic enrichment disabled. Retained checkpoints
exist, but there is no safe user-facing synthesis-only recovery.

Publish reusable verified results with explicit synthesis status, and make
recovery operate on the exact source, provider, receipts and accepted token
budget. Do not turn cancellation, authorization loss or an ambiguous external
outcome into permission to issue another request.
Fallback publication must retain source-revision, execution-ownership,
cancellation and authorization fences; synthesis-only recovery must make zero
section calls.

### NR03 — grounding must cover every displayed claim

`backend/app/services/report_grounding.py:185–198` ignores image tokens.
`web/src/pages/ReportMarkdownText.tsx:23` and
`backend/app/services/report_markdown.py:126–127` render their alt text as an
image-omission description. Validation accepted this body while counting only
the first claim block:

```markdown
A referenced observation. [S1]

![The attack compromised 927 customer organizations.](https://example.com/chart.png)
```

The second claim is visible without a source citation. This is a structural
validation/rendering mismatch, not a claim that structural checks can prove
factual support. Extend the shared Markdown corpus to image descriptions,
including citation-looking text inside them and HTML/PDF parity.

### NR04 — hunt schedule drafts need a lifetime beyond their row

`web/src/pages/HuntReviewSchedule.tsx:34–41` stores the baseline and draft only in
component state. Filter changes in `web/src/pages/TeamHuntWorklist.tsx:85–111`
replace visible rows. In Chromium, Firefox and WebKit, setting priority to
urgent and entering a deadline, filtering the row away, and returning to it
produced no discard dialog and restored normal priority/an empty deadline.

The captured baseline version is correct; this is draft lifetime loss, separate
from the previous report-schedule fix. Preserve the baseline with any retained
draft. Cover queue pagination, team changes and a polling update that removes a
dirty row, as well as ordinary route navigation.

### NR05 — explicit denial is different from an unverified snapshot

`web/src/pages/OperationsPage.tsx:59` uses retained query data directly;
lines 117–122 classify all failed refreshes as last-known state, and line 303
suppresses the actual error when that state exists. Workers, history and runs
also pass raw cached data to child views.

All three browser engines reproduced 200 → 403 while the session endpoint still
had its last successful response. Prior database-health content remained, and
the explicit permission-revoked message disappeared behind a generic stale
warning. Backend checks and no-store headers worked. This is previously
authorized data remaining in the same browser, not demonstrated cross-account
exposure or a fresh unauthorized API read.

Use terminal-access-aware data handling, surface the denial and disable protected
actions. Preserve the intentional last-known presentation for transient 503 or
network failures. Test these transitions separately with actual Query behavior.

### NR06–NR08 — mobile controls and focus need behavioral checks

- **NR06:** `web/src/pages/DashboardRssItem.tsx:119–154`. At 390px, a fetched,
  read, unstarred, badge-free article in default compact mode has a detail button
  measuring **374 × 0px**, without a name. The title opens the publisher. Keep
  internal details and external source/preview actions individually discoverable.
- **NR07:** `web/src/pages/DashboardRssItem.tsx:178–203` and
  `web/src/pages/dashboardPanelPresentation.ts:35–38`. The mobile inspector is
  a fixed div, not a managed modal or route. After opening it, Shift+Tab focuses
  an underlying control whose hit-test is covered by the inspector. This was
  reproduced in Chromium, Firefox and WebKit. Cover nested preview/discard
  dialogs and resizing while open when adopting the shared focus infrastructure.
- **NR08:** `web/src/pages/OperationsHealthTrends.tsx:161`,
  `web/src/pages/OperationsCapacityTrends.tsx:15–19`, and clipping in
  `web/src/pages/OperationsPage.tsx:381`. At 390px, the capacity section grows
  to **596px** and the worker chart wrapper to **570px**. The outer container
  clips them; the wrapper has equal client/scroll widths, so it cannot recover
  the hidden area. Document scroll width remains 390px. Test child bounds and
  access to the rightmost control/data, not only page overflow.

### NR09 — scratch ownership must include the installation

`backend/app/services/export_job_scratch.py:14` creates files under a common
temporary prefix. Lines 22–34 remove any matching directory whose job is absent
or inactive in the current database. An isolated temporary-root probe using the
real helpers and two mocked database views preserved an active job under its
own view, then deleted its artifact under the foreign view while the original
claim remained running.

An expanded concurrent security test slice also saw a machine export fail with
`FileNotFoundError`; the same test passed alone. The precise deleting process in
that run was not instrumented, so the controlled probe is the definitive proof.
This affects parallel suites/development instances and shared-host deployments
using different databases with the same OS identity and temporary root. The
supplied export container has private tmpfs (`docker-compose.yml:539–540`);
cross-container data loss and loss of durable database artifacts were not shown.

### NR10 — label semantic groups rather than generic divs

Full-page mobile Axe checks report `aria-prohibited-attr` on the per-model group
at `web/src/pages/AiSettingsOverviewTab.tsx:171`. The same static pattern appears
at `web/src/pages/StatsPage.tsx:431` and `web/src/pages/AuditLogsPage.tsx:256`.
Existing graph-region audits miss the responsive replacement records. Use a
labelled semantic group/section where meaningful and whole-document audits.

### NR11 — the bundled alert misses an isolated restart

`scripts/operations/monitor.py:170–184` reports a restart only when its counter
increases relative to the preceding sample. The timer runs every 60 seconds
(`scripts/operations/examples/threatlens-monitor.timer:6`). A probe with restart
counts `[0, 1, 1, 1]` produced active incident counts `[0, 1, 0, 0]`.

The bundled `ThreatLensHostOrRecoveryObjectiveFailed` rule uses `for: 2m`
(`scripts/operations/examples/prometheus-alerts.yml:15–16`), so an otherwise
healthy isolated restart resolves before firing. Prometheus requires the
condition to remain active through the configured pending interval; see the
[official alerting-rule documentation](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/).
An explicitly configured HTTPS receiver is attempted during that observation,
independently of Prometheus's pending interval, but the sample monitor
configuration has no receiver. Repeated restarts or persistent
unhealthy states can still alert. Add an event-aware restart rule and a test
covering the timer, observation lifetime and rule together, including counter
resets, recreation and unavailable observations.

## What is present and holding up

| Area | Reviewed controls and remaining limits |
|---|---|
| Authentication / team boundaries | Server-side sessions, CSRF, scoped credentials, current membership/permission checks, OIDC validation and explicit MCP OAuth consent/PKCE/resource binding. No new bypass demonstrated. Real enterprise IdP/ingress configuration still needs deployment qualification. |
| Outbound security / previews | Vetted-IP pinning, shared-address/private-network default restrictions, total deadlines, decoded-size limits, credential/destination binding, sandboxed previews and explicit external-resource preference. A global internal-network opt-in is broader than per-feed allowlists. |
| Durable automation | Ownership/credential fences, stable action IDs, receiver policy-head checks, fair callbacks, reconciliation progress and acknowledgements are present. External vendor acceptance and a later local policy change cannot be atomic. |
| Team and AI features | Team ownership/integrations, scoped receiver credentials, assessed evidence, hunt worklists/schedules, provider routing/quotas/qualification, publication consumers and MCP evidence pagination are implemented. AI section/synthesis recovery remains incomplete as above. |
| Database / workers | Separate migration/runtime privileges, operation budgets, pool limits, lock ordering, TCP liveness settings and dedicated worker classes are present. Actual aggregate capacity still depends on deployment topology and hardware. |
| Containers / recovery | Read-only roots where supported, bounded temporary storage, dropped capabilities, no-new-privileges and CPU/memory/PID limits. Current-schema independent-copy/key reconstruction passed on disposable local resources. |
| Logging / errors | Correlated request/task fields, safe public error references, secret redaction, SQL parameter hiding, access-log query removal and bounded diagnostics. Explicit access-loss messaging needs NR05. |
| Modularity / tests | Typed worker dependencies, selected import-boundary contracts, source-size/complexity gates and substantial backend/browser tests exist. The new bugs cluster at interactions between individually tested components. More shared lifecycle and recovery contracts will help more than indiscriminate splitting of modules. |

The [separate security report](../../security_best_practices_report.md) records
the inspected controls and security-test scope in more detail.

## UI/UX improvements beyond defects

1. **Make team hunts a focused workspace.** The shared-view copy form currently
   precedes the hunt queue (`TeamsPage.tsx:230–249`); the first hunt is roughly
   900px down even on desktop. Keep team identity, queue scope and work at the
   top, and move configuration into a separate area.
2. **Split AI settings by task.** The configuration has nested sidebars and a
   narrow central form (`AiSettingsPageView.tsx:269`,
   `AiSettingsConfigurationTab.tsx:16–48`). Inspected states measured about
   5,900–7,000px tall on desktop and 8,500–10,000px on mobile. Separate provider
   routing/quotas from feature budgets, context and prompts, retaining drafts
   across sections and a scoped save/validation summary.
3. **Persist analytics scope.** Ingestion date/feed choices are component state
   (`StatsPage.tsx:82–89`). URL-backed filters would preserve/share the exact
   trend across navigation, matching the stronger hunt-queue behavior.
4. **Link operational evidence directly.** Qualification runs and publication
   previews should link to the exact run, receipt or source passage instead of
   sending users to another area to search again (`AiProviderQualification.tsx:66`,
   `ReviewedPublicationPanel.tsx:158`).

## Verification and scope

- Fresh full backend: **4,359 passed / five optional skips**, in 23 minutes
  20 seconds. Three skips require the separately installed official MCP SDK;
  two are explicit capacity/recovery harnesses. Combined line/branch coverage
  is **86.85% overall / 87.08% reporting**, and every critical-module floor
  passed. Statement coverage is 89.83%; branch coverage is 75.24%. This is one
  complete run against the final reviewed application source, not a combined
  measurement from multiple revisions.
- Fresh frontend: **158 files / 1,278 tests passed**. ESLint, TypeScript/Vite
  production build and production-bundle login smoke passed.
- Python Ruff and the source-size gate passed across **987 production files**.
- Generated API Markdown and OpenAPI documents match current source; verification
  compared generated content in memory without rewriting the tracked artifacts.
- Dependency audits: npm policy audit passed; Python runtime audit reported no
  known vulnerabilities. This is not a fresh OS-image vulnerability scan.
- UI review: **11 existing Chromium workflows** and **22 desktop/mobile states**
  at 1440/390px, plus **15 observational probes** across Chromium, Firefox and
  WebKit. These probes deliberately measure the defects above; their successful
  execution does not mean the defective behavior meets acceptance criteria.
  Five focused Markdown rendering tests also passed.
- Security slices: **206 passed**; expanded slice **179 passed / one scratch
  failure**, with the failed machine-export test passing in isolation. The
  controlled scratch probe independently confirmed NR09.
- Two real PostgreSQL AI probes passed their defect-demonstration assertions:
  automatic dispatcher recovery and fully covered synthesis failure. Provider
  responses were synthetic; no paid AI or production SIEM work was launched.
- Recovery unit suite: **125 tests, four opt-in skips**. Operations suite:
  **20 tests, one opt-in skip**. The previously skipped host-loss reconstruction
  was then run explicitly against an exact-current-source image and passed at
  migration `0124_reconciliation_progress`. It restored encrypted data after
  deleting the original disposable volumes/credentials, used an independently
  retained key, kept outbound operations quarantined and verified runtime
  credentials lack schema-creation privilege.

The recovery image reuses an existing local runtime image with the exact same
locked dependency hash, overlays current source, and verifies its source digest
inside the fixture. It is a test image, not a rebuild of the live stack.

Retained evidence: [validation measurements](evidence/2026-09-27-post-fix/validation.json),
[current-schema host-loss drill](evidence/2026-09-27-post-fix/host-loss.json),
[restart-alert timing](evidence/2026-09-27-post-fix/restart-alert-probe.json), and
[scratch ownership probe](evidence/2026-09-27-post-fix/scratch-isolation-probe.json).
Browser screenshots show the [compact article](evidence/2026-09-27-post-fix/mobile-compact-article.png),
[mobile inspector](evidence/2026-09-27-post-fix/mobile-article-inspector.png),
[clipped Operations trends](evidence/2026-09-27-post-fix/mobile-operations-clipped.png),
and [retained snapshot after 403](evidence/2026-09-27-post-fix/operations-after-403.png).
Displayed users, operational values and API version/schema strings in these
screenshots are synthetic fixtures, not live deployment measurements.

The existing local API/web images were created on **2026-09-17**, report source
revision `unknown`, and have no application source mounts. Their healthy status
does not qualify `5e847a8`. This pass used disposable databases/recovery services
and an isolated browser app with mocked HTTP responses. Real-server browser,
OpenSearch contract, populated migration and short sustained-load evidence from
the preceding fix pass is documented in [the fix report](2026-09-27-review-fixes.md);
those are prior measurements, not newly repeated tests here.

## Remaining deployment qualification

- Run sustained mixed workloads and disjoint large exports through the intended
  ingress, prefork worker topology, database and SIEM authorization/TLS setup.
  Local short/threaded fixtures do not establish production latency, memory,
  lock-wait or recovery objectives.
- Exercise real off-host restoration on an independent target and verify
  deployment-specific backup objectives and alert delivery. The local drill
  explicitly records `production_qualified: false`.
- Qualify actual IdP claim mappings, proxy trust and TLS/cookies, approved model
  capabilities, and analyst-reviewed AI quality gates. Connectivity and structural
  citation checks cannot certify interpretation quality.
- Run manual NVDA/JAWS or VoiceOver workflows for article inspection, nested
  dialogs, queues, account recovery and report source navigation. Automated Axe
  checks and browser keyboard probes do not replace assistive-technology testing.

Next implementation should fix NR01–NR09, then NR10–NR11, promoting the observed
failures into durable regression tests. Preserve existing authorization and
external-side-effect safeguards while centralizing repeated lifecycle behavior.

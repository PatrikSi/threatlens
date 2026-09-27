# Post-review corrections — 2026-09-27

This implements all eleven findings in the
[post-fix review](2026-09-27-post-fix-review.md) and its four practical workflow
recommendations. Changes are committed on `main` as `Patrik <patrik@local>`.
They have not been pushed or deployed to the existing local stack.

## Corrections

| Finding | Implemented behavior | Regression coverage |
|---|---|---|
| NR01 · AI recovery | Automatic repair resumes the original accepted task, provider, section checkpoints and budget. Terminal or ambiguous receipts, missing proof, changed sources, cancellation and exhausted recovery allowances cannot authorize fresh calls. | PostgreSQL sweep-to-worker tests, continuation authority, changed execution mode, receipt states and retry ceilings. |
| NR02 · Synthesis resilience | Verified extraction can publish with an explicit degraded-summary disclosure. Synthesis-only recovery preserves completed sections and requires proven-not-sent receipts and sufficient existing allocation. | Real database/API tests for synthesis failure, fallback fences, complete coverage, cancellation and budget preservation; continuation UI tests. |
| NR03 · Report grounding | Visible image descriptions participate in claim validation. Citation-looking image text cannot masquerade as a navigable reference. | Shared Markdown corpus across validation, web, HTML and PDF; unsafe URL and encoded/nested text cases; browser rendering without third-party requests. |
| NR04 · Hunt drafts | Filters, pagination, team changes and navigation coordinate discard confirmation. Edited rows remain mounted when polling removes them, with protected actions paused. | Real Router/Query tests and three-engine browser workflows, including captured schedule versions and retained drafts. |
| NR05 · Operations access | Explicit denial hides all cached operational datasets and disables diagnostics. Later transient errors cannot restore denied content; deliberate successful refresh restores the workspace. | Per-dataset denial tests, pending diagnostic completion, and browser 200 → 503 → 403 → 503 → 200 transitions. |
| NR06 · Mobile triage | Compact cards always have a visible, named article-detail control with a usable touch target. | Badge-free, read-article browser checks at mobile width. |
| NR07 · Inspector focus | Mobile details use the shared dialog stack. Inspector presentation remains stable until close, preserving nested local editors through resizing. | Keyboard cycling, background isolation, nested confirmation, resize and opener-focus restoration in Chromium, Firefox and WebKit. |
| NR08 · Responsive operations | Grid children constrain their minimum width; chart/table wrappers own horizontal overflow. | Child bounding boxes, keyboard scrolling and mobile control reachability. |
| NR09 · Scratch isolation | Export scratch belongs to the actual database connection namespace, with a private owned directory. Cleanup does not scan foreign or legacy flat paths. Each pytest session also gets a private temporary root. | Independent database/role/connection-option namespaces, password rotation, unsafe directory rejection and same-database cleanup. |
| NR10 · ARIA semantics | Responsive AI, ingestion and audit record collections use labelled group semantics. | Whole-page mobile Axe checks in three engines. |
| NR11 · Restart alerts | The monitor retains the observed restart timestamp independently of incident lifetime. A dedicated alert fires immediately within its five-minute observation window. | Monitor state transitions plus pinned Prometheus `promtool` rule tests in local validation and CI. |

Independent review identified two further inspector/queue details before
completion: nested local dialogs must survive breakpoint changes, and a retained
hunt's reminder acknowledgement must appear disabled when its action is paused.
Both are included in the lifecycle correction.

## Workflow improvements

- Team hunts have a focused workspace; team setup, AI context, governance and
  suppressions have separate destinations. Existing dirty-state guards remain.
- AI configuration uses task disclosures and the full content width. Collapsing
  a section preserves its draft. Shared save controls remain reachable; validation
  and report-budget links open the required disclosure before moving focus.
- Ingestion statistics retain date/feed scope in the URL, supporting reload and
  browser history. Missing or inaccessible selected feeds remain explicitly
  scoped rather than silently broadening the query to all feeds.
- Qualification records link to `/settings/ai?run=<UUID>`. A linked run remains
  selectable outside the current history page. Same-page links preserve drafts;
  leaving the workspace still invokes its discard guard.
- Reviewed publication previews open a bounded, selected-indicator evidence
  dialog. Passages appear only when source/extraction/review revisions, malicious
  verdict, expiry and suppression state still match the preview. Changed evidence
  requires refreshing the preview. Selected evidence reads hold shared source
  and extraction-state locks so concurrent publication cannot mix revisions.
  The dialog shows retained extraction passages and team rationale; supplemental
  current AI quotations are excluded because they are not in the publication
  snapshot. Publication history exposes selectable IDs.

The optional `ioc_id` filter on `GET /items/{item_id}/indicators` is additive and
uses the existing authorization and response contract. Generated API reference
and OpenAPI artifacts have been updated. No database migration is required.

## Verification

The final frontend run passed **1,335 tests in 160 files**. ESLint, application
and browser TypeScript checks, the production build and the production-bundle
login smoke test passed. Source-size and backend Ruff gates passed without
loosening their limits. npm reported zero vulnerabilities; pip-audit found no
known runtime vulnerabilities.

The real-server matrix validated **57 workflows across three engines**, with
AI-enabled provider/statistics cases run in their separate fixture mode. An
initial Firefox export case timed out during login under concurrent validation;
its isolated rerun passed in 13.9 seconds. Chromium's AI provider case encountered
`ERR_NETWORK_CHANGED` during disposable Docker network changes and passed after
container creation/cleanup was serialized. Neither interruption was hidden with
automatic retries or a longer per-test timeout.

Operations tests completed with **23 passes and one opt-in host-loss skip**, and
the official, pinned Prometheus `promtool` passed the restart alert rule tests.
Generated API and browser preview-policy artifacts match the source.

The complete backend suite reported **4,416 passes and five skips**, taking
28 minutes 21 seconds. Its combined line/branch coverage was **86.93%**;
reporting coverage was **87.23%**. The dedicated coverage quality gate passed.
This measurement precedes the final selected-evidence read fences; those were
then verified with **76 passing related tests, exit 0**, including three new
PostgreSQL concurrency/cache tests. They observe blocked writers through
`pg_blocking_pids`, check source/AI-state publication, refresh a cached ORM
revision, and exercise a credential without write permissions.

The full backend execution tool returned **143** after emitting the complete
passing pytest summary and coverage JSON. Its exact termination cause was not
established. No pytest process or newly created test service remained; a bounded
review found no evident process-group termination defect in the relevant tests.
The full run's test/coverage results are retained, but its wrapper exit is not
reported as a clean zero exit. The later backend slice exited normally.

The complete mocked-browser matrix passed **132/132 cases** across Chromium,
Firefox and WebKit, with one worker and no retries or timeout increases. The
final publication-evidence presentation change then passed **21 focused DOM
tests and six affected browser cases**, including accessibility, focus return,
exact revisions, denied access and exclusion of supplemental AI quotations.
ESLint, the production build and bundle smoke test were rerun successfully after
that final presentation change.

Focused regression tests accompanied each implementation commit; the
[UI lifecycle report](2026-09-27-ui-lifecycle-fixes.md) records its initial
cross-browser checks.

Local verification logs: `/tmp/tl-nr-fixes-backend.log`,
`/tmp/tl-nr-fixes-coverage.json`, `/tmp/tl-nr-fixes-frontend-final.log`,
`/tmp/tl-ui-full-final-browser.log`,
`/tmp/threatlens-indicator-snapshot-regressions.log`, and
`/tmp/tl-ui-evidence-final-browser.log`. Browser artifacts are retained under
`/tmp/tl-ui-fix-browser-results/`. Temporary files are local review evidence, not
durable CI artifacts.

## Deployment notes and limits

- Deploy the host monitor and its updated Prometheus rules together. Configure
  scrape/evaluation/notification delivery to fit the documented observation
  window; monitoring cannot observe restarts while it is unavailable.
- Old flat export scratch directories are deliberately not adopted or deleted
  by the new cleaner. Remove them only after all workers sharing that temporary
  root have stopped, or restart an isolated container with ephemeral scratch.
- Provider failure/recovery tests use synthetic transport. No paid AI requests
  or production SIEM hunts were launched. Browser server tests use disposable
  PostgreSQL/Redis, local application services and controlled identity fixtures.
- Target-hardware sustained capacity, actual enterprise IdP/ingress behavior,
  independent off-host restoration and manual assistive-technology qualification
  remain deployment-specific acceptance work. These fixes do not claim that
  external qualification has been completed.

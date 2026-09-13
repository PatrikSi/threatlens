# Main merge-readiness review — 2026-09-13

This is the original pre-remediation assessment. See the
[remediation and validation record](2026-09-13-merge-remediation.md) for fixes and
the prepared 2.0.0 candidate.

**Verdict: do not merge yet. Recommend version 2.0.0 after the four blocking
findings below are resolved and the release candidate passes CI.**

## Scope and baseline

This review compared freshly fetched `origin/main`
(`b56e7070d12ba0bd17eab831a4d0df7bd6f2db12`) with the local `dev` candidate
(`4da997e16d175887f87ad8de570ec420e8cb6602`). The candidate contained 291 commits
and 826 changed files relative to main: 89,292 inserted and 15,734 deleted lines.
Both branches still declared version `1.10.0`.

Three independent reviewers covered backend lifecycles, frontend workflows, and
security/deployment compatibility alongside the main review. Review work included
source inspection, the full backend suite, focused regression tests, real
PostgreSQL concurrency/upgrade probes, Chromium/Firefox/WebKit workflows,
dependency audits and scans of the locally built images. The live application,
credentials and existing backups were not modified. No merge or push was made.

Small source-hygiene, generated-contract, documentation and test-fixture
corrections were committed during this review. The functional findings below
remain open; their reproductions used disposable databases and browser fixtures.

## Findings that block merge or release

| ID | Priority / area | Confirmed behavior and impact | Required correction |
| --- | --- | --- | --- |
| MR01 | P2 · Team authorization | [`lock_investigation_team`](../../backend/app/services/investigation_read_access.py#L240) checks membership in the query that waits for the team lock, without rechecking after acquisition. An OIDC-only member can create a team investigation after the membership assertion expires during that wait. | Recheck current membership after acquiring the team lock, using the common lock order. Add a real PostgreSQL creation-versus-expiry regression. |
| MR02 | P2 · Report governance | [`schedulePayload`](../../web/src/pages/useReportingController.ts#L764) omits `review_required`. Saving the editorial-review toggle reports success but preserves the previous policy. A legacy automatic schedule can continue publication/delivery despite the operator enabling review. | Include the submitted review policy in create/update payloads. Exercise both toggle directions, persistence and reopening through browser/API tests. |
| MR03 | P2 · Deployment / error handling | [`useExportJobs`](../../web/src/pages/useExportJobs.ts#L79) and [`useProcessingWorkspace`](../../web/src/pages/useProcessingWorkspace.ts#L155) call `crypto.randomUUID()` unconditionally. It is unavailable in nonsecure, non-localhost HTTP contexts supported by local/LAN deployment configuration. Exports never send a request; recovery review never opens; both throw without actionable inline feedback. | Share a cryptographically secure UUID helper with a `getRandomValues` fallback, as existing AI/report paths already provide. Test an actual non-localhost HTTP origin, including retry-key reuse. |
| MR04 | P2 · Image security | The local backend image contains two fixable HIGH findings in Debian `libpcre2-8-0` 10.42-1. The [backend OS installation layer](../../docker/backend.Dockerfile#L6) only updates indexes and installs fonts, so rebuilding against an unchanged base does not guarantee a patched inherited package. This fails the existing release scan policy. | Install a verified patched package/base, confirm at least 10.42-1+deb12u1, regenerate shipped OS inventories, then scan and smoke-test the exact release digests for both architectures. |

MR01 was reproduced with a real locked PostgreSQL team row: the create operation
waited until the synthetic OIDC assertion expired, then committed an investigation
although a fresh membership predicate returned false. The reviewed update,
shared-view and triage paths already use post-lock membership checks; creation
needs the same invariant.

MR02 was reproduced twice in Chromium with an existing schedule whose
`review_required` was false. Enabling “Require editorial review” and saving
displayed “Report schedule updated.” The PUT omitted the field and reopening
showed the toggle unchecked. The API intentionally preserves omitted update
fields, so this is a client serialization defect.

MR03 was reproduced twice using isolated application fixtures with
`crypto.randomUUID` explicitly unavailable: export submission issued zero POST
requests and recovery threw before opening its confirmation. A separate Chromium
probe on the intercepted non-localhost HTTP origin
`http://threatlens-review.invalid/` reported `isSecureContext: false`,
`crypto.randomUUID: undefined` and an available `crypto.getRandomValues`,
confirming this matches native browser behavior. The complete application
workflow was not exercised at that HTTP origin. Ordinary localhost-only browser
coverage does not expose this compatibility failure.

MR04 is supported by Debian's records for
[CVE-2026-86145](https://security-tracker.debian.org/tracker/CVE-2026-86145) and
[CVE-2026-89161](https://security-tracker.debian.org/tracker/CVE-2026-89161).
Application exploitability was not demonstrated. In particular, the first
advisory concerns PCRE2 DFA matching; Python custom rules use a separate regex
implementation. Retain the current HIGH/CRITICAL release gate rather than
waiving the findings. The corresponding local web-image scan was clean under
that same policy.

## Other confirmed finding

| ID | Priority / area | Finding | Improvement |
| --- | --- | --- | --- |
| MR05 | P3 · AI statistics | [`ai_ops_metrics.py`](../../backend/app/services/ai_ops_metrics.py#L532) selects the latest `finished_at` with descending order but without excluding NULL. A queued run can make `last_ai_run_at` null even when completed runs exist. This also exists on main and is not currently rendered by the web UI. | Filter unfinished runs or use `NULLS LAST`; cover one finished plus one queued run. |

An apparent export-publication/policy-lock deadlock was also investigated. A
three-session PostgreSQL reproduction completed successfully because PostgreSQL
reordered compatible lock requests. It is not included as a defect.

## Architecture and quality assessment

| Area | Assessment | Further work |
| --- | --- | --- |
| Authorization and team ownership | Current-access predicates, credential fences, immutable ownership and publication revision checks provide useful protection. The creation race in MR01 shows why every entry point must share the same locking invariant. | Consolidate the remaining duplicated post-lock checks and expand expiry-under-contention coverage. Team workspaces do not constitute complete tenant isolation for every global resource. |
| Workers and recovery | Durable execution ownership, bounded requests, dispatch reservations, separate export slots and cancellation/recovery tests address the previous major lifecycle risks. | Continue replacing orchestration-facade dependencies with narrow contracts as modules change. Add adversarial transition coverage when introducing new work types rather than relying on nominal queue tests. |
| Frontend state and accessibility | Session-aware caching, retained draft baselines, stacked dialogs and keyboard-accessible chart tables are substantial improvements. Browser probes still found two gaps missed by ordinary component tests. | Test serialization through persisted state and add a nonsecure LAN browser project. Keep real-server identity transitions; supplement automated accessibility checks with human screen-reader use. |
| API and compatibility | Generated route/schema comparison found no removed operations or newly required request inputs. The new review defaults nevertheless change observable report publication behavior. | Document behavioral defaults as part of the API contract, alongside additive schema checks. |
| Errors and logging | Typed provider/deadline failures, request references and secret-redaction coverage are present. MR03 bypasses normal mutation error handling entirely. | Extend user-facing failure checks to synchronous preparation steps and uncommon deployment contexts. |
| Data and deployment | The populated main-schema upgrade and runtime/migration role split passed in isolation. New queues and ownership semantics require coordinated deployment. | Qualify the exact release images and preserve the offline role-cutover/backup runbook. Do not roll old workers alongside the new team/report semantics. |
| Capacity | Bounded queries, SQL aggregation, response budgets and isolated export capacity are implemented. Existing enterprise measurements are short smoke evidence. | Compare sustained mixed workloads, large disjoint exports, lock waits, queue recovery and memory on intended hardware. This review does not establish a new production capacity limit. |
| Maintainability | Source-size/complexity gates pass and extracted lifecycle modules improve inspection. Some large controllers and compatibility facades still concentrate cross-cutting invariants. | Prefer incremental extraction with typed contracts and lifecycle tests. Avoid a broad pre-release rewrite unrelated to a demonstrated defect. |

## Corrections committed during review

All commits use `Patrik <patrik@local>` and the repository's imperative message
style.

| Commit | Correction |
| --- | --- |
| `2d01d1a` | Removed aggregate source whitespace errors, wrapped chart JSX to satisfy the source gate, and regenerated the AI statistics API artifacts. Six newly added response fields had been missing from the checked-in contract. |
| `50d5521` | Updated the provider browser workflow to use the renamed Statistics navigation. |
| `65683a1` | Put the offline database-role cutover before upgrade commands, included export workers in coordinated shutdown, and corrected release documentation about installed OS packages. |
| `f85dc57` | Corrected the provider-usage fixture's exclusive end boundary and isolated the capacity subprocess from externally configured test-service URLs. Runtime behavior was unchanged. |
| `9c1ef3c` | Put intended latency samples inside the reporting window and explicitly tested that an at-until success cannot alter the median or latest-success timestamp. |

All 33 tests across the three corrected test files passed together after these
changes. The complete suite was not repeated after these test-only corrections.

## Validation and its limits

- Full backend run: **3,191 passed, 5 failed, 2 skipped** in 11 minutes 2 seconds.
  All five failures were diagnosed as fixture defects and corrected in
  `f85dc57` and `9c1ef3c`; **all 33 tests in the affected files passed together**
  afterward. The production reporting window deliberately uses `[since, until)`;
  fixtures now test that boundary explicitly. These are full-suite results plus
  focused reruns, not a claim of a subsequent completely green full-suite run.
- Coverage: **85.97% overall**, **86.61% reporting**; all configured critical
  module floors passed.
- Independent backend review: **108 focused tests passed**, covering provider
  output, citations, deadlines, execution ownership, cancellation, recovery,
  admission and export publication.
- Frontend candidate evidence: **1,155 tests across 133 files**, lint and
  production build passed at `4da997e`. Subsequent frontend changes in this
  review were JSX wrapping and a browser navigation selector, with no runtime
  behavior change.
- Representative browser review covered **45 cases across Chromium, Firefox and
  WebKit**. The stale selector was corrected. Cases that timed out during host
  contention passed when rerun serially, including the nine-case provider and
  identity matrix. Purpose-built probes separately reproduced MR02 and MR03.
  Earlier chart validation included six cross-browser cases and automated axe
  checks with no reported violations. These checks do not establish human
  assistive-technology usability.
- PostgreSQL upgrade: main's `0085` schema with synthetic legacy data upgraded
  through `0105` after the explicit role cutover. Data remained intact, migrated
  table ownership was correct, runtime DML succeeded, and runtime DDL was denied.
- Python audit: **93 locked runtime packages, no known vulnerabilities**.
  JavaScript dependency audit: **no known vulnerabilities**. The runtime lockfile
  reproduced exactly.
- Trivy scan of local images with a refreshed advisory database and the existing
  fixable HIGH/CRITICAL policy: **backend 2 HIGH, web 0**. This is separate from
  language-package audits and does not replace exact release-digest scans.
- Source-size gate (**795 production files**), Ruff, Python compilation,
  aggregate whitespace checks using CI's generated-legal-artifact exclusions,
  and regenerated API artifact consistency passed. The refreshed API contract
  contains 247 paths and digest
  `3b7cf09fa19afec6815ab219fb5acd1c24c687df0a16c3765c6d0a1c3ada3cd9`.

The first full-suite attempt hit disposable PostgreSQL startup timeouts while
the host was under severe storage I/O contention. The complete run above used
separate tmpfs PostgreSQL and Redis containers. No live-stack data was used.
The initial infrastructure errors and serial browser retries are not evidence
of an application throughput limit, nor a substitute for target-hardware tests.

The latest green remote dev CI inspected was for `b7584cb` on September 1
([workflow run](https://github.com/PatrikSi/threatlens/actions/runs/33505846824)).
It does **not** validate this local candidate or the review corrections. No new
remote workflow was triggered during this review.

## Recommended version and release sequence

Use **2.0.0**. Most endpoint additions are structurally backward compatible, but
this release changes both upgrade requirements and externally observable report
behavior:

1. Existing installations need new runtime/migration database credentials and
   an explicit offline ownership/privilege cutover; an old environment cannot
   simply launch the new Compose configuration.
2. Newly created manual reports default to editorial review in
   [`report_storage.py`](../../backend/app/services/report_storage.py#L61).
   Newly created schedules default `review_required` to true in
   [`ReportScheduleCreate`](../../backend/app/schemas/reports.py#L315).
   [`report_generation.py`](../../backend/app/services/report_generation.py#L847)
   only automatically publishes when review is not required and only emits
   requested delivery once published. Existing clients that omit the new policy
   can therefore see generation succeed without the prior automatic publication
   and delivery behavior. Existing stored legacy schedules preserve their policy.
3. Twenty migrations, team-aware workers and the report v3 queue require a
   coordinated API/worker upgrade rather than a mixed-version rollout.

The behavior change is the strongest reason for a major version under
[Semantic Versioning](https://semver.org/), rather than the number of features or
commits. If these compatibility changes were removed or explicitly kept opt-in,
`1.11.0` would be a reasonable alternative; that is not the current implementation.

Before merge/release:

1. Resolve MR01–MR04 with their targeted regressions and refreshed image artifacts.
2. Set the version using `./scripts/set-version.sh 2.0.0`, regenerate API and
   dependency/compliance artifacts, and review the final upgrade/release notes.
3. Run quality gates on the intended candidate SHA, including the complete
   corrected suites and populated migration/container checks.
4. Qualify capacity-sensitive changes on intended hardware using comparable
   committed refs; preserve successful sample counts and recovery measurements.
5. Verify the exact amd64/arm64 release image scans and smoke tests before
   promotion. A push to main can publish mutable `main`/`latest` images after
   gates pass, so merging is also a release action.

No version bump or tag was created by this review. Current release tooling
expects the stable `X.Y.Z` / `vX.Y.Z` format.

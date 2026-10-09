# Final local review — 2026-10-08

This report records the Oct8 checkpoint. Current instance and qualification
results are recorded in the [Oct9 review](2026-10-09-final-review.md); the
revisions, counts and limitations below retain their original applicability.

At that checkpoint, the owned ThreatLens **2.1.0** instance was running at
**http://127.0.0.1:3001**. The application candidate was
`31eb1bc698f1b3a246160863ccde144cafd05d9b`. This follows the
[remediation checkpoint](2026-10-08-remediation.md) and records the additional
source and production-browser review, fixes and qualification separately.
Every change was committed as `Patrik <patrik@local>`.

**Decision: confirmed local defects are corrected; merge qualification remains
open for comparative capacity and intended-source hosted quality/platform
checks.** Version 2.1.0 is already prepared. No further version bump is needed
for these pre-merge corrections. No remote push, merge, tag, public image or
model promotion was performed during that checkpoint.

## Further findings corrected

| Finding | Correction and regression proof | Commit |
|---|---|---|
| A passing topology workload could remain green after teardown failed | Discover exact owned labels, attempt every removal with bounded verification, preserve workload/diagnostic failures and fail qualification on cleanup failure. | `0438306` |
| Disposable helpers could follow a remote or changing Docker context | Validate a local Unix endpoint before creation; bind creation, inspection and cleanup to it. The independent bounded fleet observer also receives that same environment. Normal operator monitoring retains its selected context. | `fdf3ff0`, `ec47219` |
| Qualification worker result disagreed with persisted contract failure | Return the committed ready/error outcome. | `3e48eb7` |
| Cancellation or replaced delivery was reported as a qualification error | Error finalization returns the committed outcome/reason; canceled, superseded and removed work is skipped, while genuine failures remain errors. Four cancellation/supersession regressions failed before correction; twelve outcome cases pass. | `1f50226` |
| Automation attachment offered destinations the actor could not edit | Apply current global permissions plus investigation owner/editor, archived state and team-write rights; preserve readable findings. Five permission regressions cover changes in actor access. | `831fd2f` |
| Two dark native selects failed Chromium text contrast | Explicitly style Consumer/Publication select backgrounds. Measured baseline was 4.260059:1; the corrected controls pass the unchanged 4.5:1 threshold across engines. Other inspected inputs already passed and were left alone. | `831fd2f` |
| Access posture metrics had invalid definition-list grouping | Render direct term/value children in permitted groups, preserving labels, counts and layout. Native Axe reproduced `definition-list`/`dlitem` in all engines and both themes. | `fdbabf3` |
| Bounded article readers could not receive keyboard focus | Add named focusable regions for summary/full text, using the existing focus outline. Firefox reproduced `scrollable-region-focusable`; native checks verify Tab focus and actual text scrolling. | `fdbabf3` |
| Access governance expanded the 390px mobile page to 796px | Constrain the base grid track, retain desktop columns and confine the 760px table to its own named keyboard-scrollable region. Six before-fix browser cases fail; all six pass afterward. | `31eb1bc` |

## Actual runtime and image provenance

The local fixture uses synthetic credentials and four owned research articles.
Its private credential/configuration files, cookies, consent requests, raw logs
and screenshots are outside the repository. Existing `.env` files,
`backend/uv.lock`, `backups/` and unrelated services were preserved.

There are eleven running services and one successfully completed dedicated
migration service. All five worker pools use concurrency one. Actual container
inspection verifies CPU, memory, PID and no-swap limits and read-only roots.
The API is ready, migration head is `0125_webhook_article_text`, and the live OpenAPI document matches
the checked-in 2.1.0 contract. Its versioned contract anchor remains
`a608ae21656a8d50f83919e01a16751720c1199a7f4cd552465770bd3df16aaa`.

| Native AMD64 image | Actual source revision | Image ID |
|---|---|---|
| Backend | `1f50226feda5c784add911faf9e9ad4f21c628f7` | `sha256:31bed1c414e6c78417cfb0eaf4783793e8e1a29eff9670a0efb19f4f73bb089e` |
| Web | `31eb1bc698f1b3a246160863ccde144cafd05d9b` | `sha256:e974899db4af6110f19772c881716dcd21028f295e294d3e44aabd22719bd462` |

The mobile amendment changes only the web page and browser regression. Backend,
backend Dockerfile and Compose Git objects are identical between these
revisions; the backend image retains its true build label. Both builds used
tracked archives without local environment files, the untracked developer
`backend/uv.lock`, browser caches or backups.
Build jobs were capped at one CPU/1536 MiB with no swap and their owned builder
containers/state volumes were removed.

Backend/worker/source networks are internal; only the web has ingress, published
on loopback. CSP permits the owned source frame plus self. Browser tests block
outside origins. AI is enabled with no configured external provider; no paid
provider, SMTP send or external webhook action was executed.

## Validation and review scope

| Check | Result and applicability |
|---|---|
| [Frontend unit suite](evidence/2026-10-08-final-local-review/source-checks.json) | 1,362 tests/164 files pass at the current tracked web source. |
| [Source checks](evidence/2026-10-08-final-local-review/source-checks.json) | Full frontend ESLint, browser TypeScript and backend Ruff pass; source-size gate passes for 1,004 production files. |
| [Dependency audits](evidence/2026-10-08-final-local-review/source-checks.json) | Required npm audit gate passes; all-severity audit reports zero vulnerabilities. |
| [Browser fixture interactions](evidence/2026-10-08-final-local-review/frontend.json) | 147/147 pass, 49 per engine, one worker and unchanged 30s/5s budgets. These use tracked Vite/test API controls, separately from live-image checks. |
| [Production UI assertions](evidence/2026-10-08-final-local-review/frontend.json) | 33/33 pass on the actual current web image; zero Axe violations. The original raw runtime gate and separate WebKit probes are described below. |
| [Backend qualification/recovery](evidence/2026-10-08-final-local-review/backend-focus.json) | 43/43 pass with real disposable PostgreSQL/Redis; no skips or cleanup remnants. |
| [Operations helpers](evidence/2026-10-08-final-local-review/operations.json) | 70 pass/one explicit opt-in host-loss skip; 17 topology and 11 proxy cleanup checks also pass without HTTPX. Exact file/object hashes establish current-source applicability. |
| [Live HTTP workflows](evidence/2026-10-08-final-local-review/http-workflows.json) | 34/34 pass through the current native backend: auth/CSRF, investigation version conflicts/archive, OAuth consent/PKCE/replay, MCP-only audience and revocation. |
| [Image inventory/startup](evidence/2026-10-08-final-local-review/helpers.json) | Dependency/legal inventory and both non-root writable/read-only web startup modes pass on the actual images. |
| [Image policy scans](evidence/2026-10-08-final-local-review/image-scans.json) | Both actual images pass unchanged HIGH/CRITICAL, ignore-unfixed vulnerability/secret policy; zero findings under that policy, using an unexpired database. |
| [Official MCP proxy](evidence/2026-10-08-final-local-review/helpers.json) | All ten required named checks pass with SDK 2.2.0, HTTPX2 2.7.0 and Python 3.12.3. Disposable proxy uses current read-only application/schema/nginx source overlays; live HTTP/browser checks independently exercise native images. |
| [Upstream comparison](evidence/2026-10-08-final-local-review/upstream.json) | Fresh read-only fetch: no incoming main commits; the application source is 179 commits ahead/zero behind. |

The broad production UI review covered twenty-one main/settings routes, six
mobile routes, light/dark themes and Chromium/Firefox/WebKit, plus article/team
navigation, provider drafts, investigation notes/history/archive, publication,
MCP consent and real worker-generated export download/deletion. It completed
234 checks: 222 passed, seven checks exposed the first two product defects and
five exposed driver problems. This finding run is not relabeled as a green run.
Three created investigations were archived and three exports deleted; no
cleanup failures or API 5xx responses occurred.

The initial rebuilt 33-check follow-up passed 27 and found the mobile overflow
in six checks. The final native recheck verifies all affected surfaces against
the actual corrected web image, including confined table/reader scrolling.
Its bounded checks retain the same 30-second deadline. All 33 UI assertions pass
(11 per engine), with zero Axe findings. The original aggregate runtime-error
gate exited 1 for one retained WebKit diagnostic; that run is not recast as an
all-green aggregate.

Axe injected scripts into the deliberately script-disabled publisher iframe,
causing driver timeouts and sandbox console messages. The corrected driver
excludes only that frame from injected analysis and still verifies real frame
text, title, sandbox, close focus, Escape and opener focus. It audits the app
shell/drawer. Theme initialization is restricted to the top-level app origin.
The outdated Account selector was corrected to the shipped My account label.

A separate instrumented WebKit probe reproduced that exact network diagnostic
when the old document started `/api/v1/auth/me` between `beforeunload` and
`pagehide`; the replacement document received HTTP 200. An explicitly caught
read-only request during unload reproduced the same browser diagnostic, without
a DOM error, unhandled rejection or CSP violation. Ordinary AbortController
cancellation followed a different path. This supports a retiring-document
diagnostic rather than an uncaught application failure, and applies only to
that reproduced lifecycle. The original native event lacked lifecycle
instrumentation and is preserved. Five routes using real in-app navigation
then passed with one document, ten HTTP 200 auth checks and zero captured
network/runtime/CSP failures; generic error listeners remained active.
No general auth or access-control error suppression was added.

Zero-check launcher/cache attempts remain unqualified private artifacts.

The earlier parallel interaction attempt encountered timeouts under substantial
shared-host load; its subsequent serial 141-case run passed with unchanged
test/expect budgets. The final source has two additional light/dark browser
cases, and its full interaction run is recorded independently. This observation
does not establish the cause of the earlier timeouts.

The historical full backend result remains **4,569 passed/two explicit harness
skips**, with **87.04% overall/87.23% reporting coverage**, at its recorded
earlier source. It is not a new full-suite or coverage run at this candidate.
The changed qualification service/worker has fresh focused validation. Earlier
OpenSearch and host-loss qualification retains its actual source/image IDs in
the linked remediation report; local fault-domain fixtures do not qualify an
operator's production backups or vendor permissions.

New repository evidence uses a field allowlist: source/object hashes, immutable image
IDs, numerical results, safe named checks, bare route paths and cleanup counts.
It contains no credentials, private configuration, OAuth query parameters,
resource IDs or raw response/log bodies.

## Remaining merge and release gates

1. **Comparative capacity on a stable isolated runner.** All four unchanged
   600-second workloads pass absolute budgets, but both comparisons flag the
   unchanged 20% threshold. First-pair export p95 rises 52.512%; reverse-pair
   governance/queue/fetch/drain/depth flags remain. Reverse drain is
   14.059 → 23.981 **ms** (+9.922 ms, +70.574%), not seconds. These are
   review-remediation comparisons in a common runtime, not a full released
   2.0 → 2.1 stack comparison. No threshold was widened or metric suppressed.
   See the [retained capacity evidence](2026-10-08-remediation.md#qualification-evidence).
2. **Intended-source hosted quality/platform checks.** Python and
   JavaScript/TypeScript CodeQL plus source AMD64/ARM64 build, scan and smoke
   still need exact-source hosted results. Local native AMD64 success does not
   substitute for ARM64 or hosted analysis. The quality workflow is available
   on pull requests/dev pushes/reusable calls; no remote write was made here.
3. **Public release promotion.** Scan/smoke the exact published platform digests
   before promotion. Main pushes, version tags and manual publication dispatches can push platform
   digests before scan/smoke; manifest promotion follows those checks. Local
   review did not push, tag or dispatch publication to obtain a check.

Optional model semantic promotion separately awaits captured chosen-model
outputs and named human review of the twelve prepared cases and individual
claims. Ordinary ingestion remains independent of that promotion. Offline
packages and provider-contract tests do not supply human/model quality approval.

## Evidence index

The compact records are [runtime](evidence/2026-10-08-final-local-review/runtime.json),
[builds/contract](evidence/2026-10-08-final-local-review/builds.json),
[source checks](evidence/2026-10-08-final-local-review/source-checks.json),
[frontend](evidence/2026-10-08-final-local-review/frontend.json),
[focused backend](evidence/2026-10-08-final-local-review/backend-focus.json),
[operations](evidence/2026-10-08-final-local-review/operations.json),
[HTTP workflows](evidence/2026-10-08-final-local-review/http-workflows.json),
[image helpers/MCP](evidence/2026-10-08-final-local-review/helpers.json),
[image scans](evidence/2026-10-08-final-local-review/image-scans.json),
[upstream](evidence/2026-10-08-final-local-review/upstream.json),
[historical applicability](evidence/2026-10-08-final-local-review/historical.json)
and [open gates](evidence/2026-10-08-final-local-review/gates.json).
Source revisions, Git objects, image IDs, log hashes and cleanup counts retain
what each run actually qualified. Private raw artifacts stay outside the repository.

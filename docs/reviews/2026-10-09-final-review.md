# Iterative code review and qualification — 2026-10-09

ThreatLens **2.1.0** is prepared. The synthetic local instance at
**http://127.0.0.1:3001** uses the earlier `e085733b` images. The current
application/image candidate `1d057bc17529af724160c28479ca7d641b725d73`
contains all review corrections, including reduced envelope reads, the
concurrent parent-reference guard, synchronous post-login navigation and
complete administrator credential admission before database access. It also
accepts backend-valid Unicode identities in credential and administrator user
forms, conceals supplied settings in validation errors, and preserves the
bootstrap script's earlier Bash compatibility. The production smoke contract now
checks the same Unicode-capable email field and required form constraints.
A fresh production build passes; the original smoke reproduces the hosted
failure and the corrected smoke passes. The seeder also recovers conflicts
raised during flush and audit creation, preserving the winning account.
Focused checks pass: 22 seed-admission/recovery cases, 96 configuration cases, 56 authentication cases, 46
administrator-user cases and sixteen bootstrap tests. Frontend lint and
TypeScript pass.

Fresh native build/runtime, HTTP/helpers and selected image scans pass at the
earlier `e085733b` source. Its original local native browser run executes all
33 checks, with **32 passed / one failed**. All six cookie-login checks pass,
including WebKit light. The remaining Firefox dark-theme failure is a
30-second screenshot timeout in the article/team workflow; its cause is
unassigned, and that surface's accessibility audit is incomplete.
The first isolated browser run fails all six logins on an invalid fixture
email. The next run at `c591c3a8` executes all 33 checks: **27 passed / six
failed**, including six successful logins. All six article checks encounter an
owned publisher 404. The fixture permission defect is corrected. The fresh native run at
`5f370197` passes all 33 checks and all article previews, but qualification
fails on two WebKit light module-loading console errors. Page errors, API
500s and cleanup errors are zero; the two console events have no timestamps
or failed-request details, so their cause remains unassigned. The
[native terminal evidence](evidence/2026-10-09-final-review/native-5f370197-listener-failure.json)
preserves the strict failure and the limits of the retained service-log tail.
[All 33 captured surfaces](evidence/2026-10-09-final-review/native-5f370197-visual-assessment.json)
were visually reviewed; no additional visual blocker was found. All 33 Axe
audits report zero violations, including six explicit publisher iframe
exclusions with separate real-content checks.
Earlier failed outcomes remain preserved under their actual source revisions.
All application corrections were committed as `Patrik <patrik@local>`.

**Decision at this checkpoint: awaiting the final native and hosted quality
results linked from [PR #31](https://github.com/PatrikSi/threatlens/pull/31).**
Consult the PR for their actual terminal results and exact tested heads;
this committed checkpoint does not predict them. No comparative threshold, resource limit, workload
duration or authorization fence was weakened. Hosted quality at `aa92f8e7`
passes all fifteen jobs. Later quality run
[38073357452](https://github.com/PatrikSi/threatlens/actions/runs/38073357452)
fails its production smoke assertion after frontend tests, lint and build pass:
it still expects the previous email input type. That run is now terminal:
**fourteen jobs pass and the frontend job fails**. Backend validation passes
4,769 cases with five deliberate skips and 87.08% coverage; all 58 module
floors pass. Its three browser jobs pass 210 cases with six deliberate split
skips and zero retries. The frontend passes 1,386 tests across 165 files; its
security audit is skipped after the smoke failure. The
[terminal quality evidence](evidence/2026-10-09-final-review/quality-7fe8cbad-terminal-failure.json)
retains actual source checkouts, CodeQL outcomes and this failure. The
[actual smoke controls](evidence/2026-10-09-final-review/production-smoke-contract-controls.json)
retain that failure and the corrected production-bundle result. The
[committed source binding](evidence/2026-10-09-final-review/production-smoke-36b2024f-source-binding.json)
verifies the tested script at `36b2024f` and unchanged application entry/build
inputs. The final
review head requires fresh qualification. The new uninstrumented capacity pair at
`c591c3a8` **passes**, with verified source, unchanged 600-second duration and
20% threshold, actual artifacts and independent cleanup. Its measured paths
remain applicable to `1d057bc1` through the
[explicit measured-path proof](evidence/2026-10-09-final-review/capacity-1d057bc1-measured-path-applicability.json):
exactly four backend paths differ, the seed
script is outside the measured execution, and Settings changes only invalid
error rendering. All other 1,285 backend objects and five capacity operation
groups match. Valid Settings outputs and schemas match in three isolated
controls. The actual measured candidate remains `c591c3a8`; whole-backend
equality and a new measurement at `1d057bc1` are not claimed. The seed
transaction intentionally differs: exactly two existing statements move into
creation recovery. Reversing only that move reproduces the original measured
transaction and the complete credential-admission reference module. The
changed seeder is never executed by the sustained measurement.

Version 2.1.0 is already aligned across the version file, frontend metadata,
OpenAPI and deployment defaults. These pre-merge corrections need no further
version bump. [Draft PR #31](https://github.com/PatrikSi/threatlens/pull/31)
receives the final review checkpoint and fresh fifteen-job quality run. Remote
main, release tags, published images and
model promotion remain unchanged. The reviewed CodeQL dispositions retain a
neutral aggregate check with the historical configuration warning.

The envelope writer correction at `aa92f8e7` passes **38/38 focused PostgreSQL
cases** and full hosted backend validation. It removes redundant reads and
revalidates parent ownership after a concurrent creator wins. The measured
variant eliminates 24 SQL executions; its instrumented total time increased,
so neither an end-to-end speedup nor the cause of earlier capacity flags is
claimed. Historical `508c7413`/`6141a719` results retain their actual inputs.
The navigation correction at `e085733b` fixes a demonstrated transient
credential-form remount. Its unchanged new regression fails before and passes
after; all **50 related authentication cases**, lint and TypeScript checks pass.
It is not claimed to explain either earlier WebKit timeout.

This follows the [Oct8 review](2026-10-08-final-local-review.md), preserving its
original sources, failures and qualification limits. The
[earlier release/source consistency proof](evidence/2026-10-09-final-review/release-consistency-e085.json)
records aligned versions, the distinct OpenAPI contract anchor/file hash,
bounded writer shutdown and exact unchanged capacity inputs. The new DOM test
is a Docker build/typecheck input, and the new images retain their actual
`e085733b` labels.
The [administrator source delta](evidence/2026-10-09-final-review/administrator-email-source-delta.json)
retains the earlier `c591c3a8` email-only correction. Current
[credential controls](evidence/2026-10-09-final-review/seed-admin-login-controls.json),
[settings controls](evidence/2026-10-09-final-review/settings-error-rendering-controls.json),
[authentication controls](evidence/2026-10-09-final-review/unicode-login-form-controls.json)
and [administrator-form controls](evidence/2026-10-09-final-review/unicode-user-create-form-controls.json)
retain their actual failing-before and passing-after results and source hashes.
The [final source assessment](evidence/2026-10-09-final-review/credential-contract-source-applicability.json)
and [frontend binding](evidence/2026-10-09-final-review/credential-contract-frontend-source-binding.json)
independently bind those tested bytes to `5f370197`. The
[portable bootstrap controls](evidence/2026-10-09-final-review/bootstrap-portability-controls.json)
retain the actual final sixteen-test result. The
[seven quality-capture controls](evidence/2026-10-09-final-review/credential-contract-final-quality-controls.json)
preserve distinct application, measured-capacity, auxiliary workflow and final
quality references; preparing evidence is not a hosted quality pass.

## Corrections completed in this pass

| Finding | Resulting behavior and regression proof | Commit |
|---|---|---|
| Retained report editorial forms could submit after write access or draft publication state changed | Current permission/state guards pause fields and direct submission; retained text and Discard remain usable. Two new regressions failed before correction; six panel/detail cases pass afterward. | `45cd540` |
| Retained investigation note edits/removal confirmations could outlive membership or archive rights | Current rights guard editing/submission. Text and Cancel remain available, and obsolete removal confirmation stays closed when rights return. Four new cases failed before; 39 cases pass afterward. | `faf607a` |
| Dirty report/workspace navigation guards disappeared when write access was revoked | Dirty private drafts retain navigation protection and explicit discard. Three regressions failed before; 14 cases pass afterward. | `98a3c13` |
| Deferred provider heading focus stole newer focus from an editor control | Preserve newer focus inside the editor; normal selection still focuses its heading. Two focus regressions failed before; 22 cases pass afterward. The hosted WebKit provider workflow subsequently passes on its first attempt. | `ff9ac24` |
| Malformed publication cursors reached UUID/date parsing with unexpected value types | Strict base64 and top-level/field validation returns the bounded cursor error. Twelve unit cases pass; native HTTP malformed-input cases are included in final qualification. | `cbb99cb` |
| Recurring schedules compared ambiguous local wall times rather than UTC instants | Weekly/monthly candidates use instant ordering while retaining existing fold/gap policy. Four regressions failed before; 11 schedule cases pass afterward. | `1629a75` |
| Worker return values disagreed with committed terminal outcomes | Qualification, team and item workers propagate committed error/supersession outcomes. Handled item errors log bounded error types. Forty-one focused cases and a separate log-sentinel regression pass. This is not a claim that every exception handler redacts all possible values. | `5b9aa3c` |
| Capacity qualification could follow remote/changing Docker contexts or succeed after teardown failed | Establish a local Unix endpoint before creation, freeze it for ownership operations and fail on incomplete/malformed cleanup. Forty-seven scoped cases pass. | `642333a` |
| Generated release upgrade commands ran migrations without stopping all writers | Stop API, Beat and every worker pool with the existing graceful deadline before dedicated migration; link the version-specific release procedure. Generated YAML and command ordering were checked. | `c4faef4` |
| SMTP protocol floor depended on runtime defaults | Enforce at least TLS 1.2 in all three verified client paths, preserving stronger defaults and verification. Three regressions failed before; 12 cases pass afterward with no network I/O. | `5859215` |
| Qualification isolation mocks relied on an ambient Docker context/import path | Make the test fixture explicit. Thirteen previously failing cases pass in a clean environment, with a separate remote-context control. Production endpoint validation is unchanged. | `de98396` |
| IOC resource measurements inherited pytest coverage tracing in child processes | Exclude only coverage bootstrap variables from benchmark children. Parent coverage, workloads and hard CPU/memory/time limits stay intact. Three boundary regressions failed before; all 55 scoped IOC cases pass under parent coverage. The earlier hosted SIGKILL was not locally reproduced or conclusively attributed to CPU exhaustion. | `985ec08` |
| Restrictive temporary frontend Dockerfile lay outside the Compose Bake context grant | Move the identical 0600 tracked Dockerfile inside that context. With matched Buildx 0.37.2 and the same exact context grant, the direct Bake probe fails before and passes after. Earlier local version probes did not reproduce the refusal and remain distinct. | `08d41ea` |
| Automatic classification/IOC/tagging recovery started while article input was still pending | Automatic derivation waits for committed fetch input; failed-fetch fallback and explicit waiting/manual recovery remain available. Six regressions failed before; all 67 new/existing integration cases pass after explicit fixture prerequisites. The initial fixed run's two fixture-selection failures are retained. | `20c289a` |
| Watchdog-selected Beat scheduler bypassed bounded canary admission | Compose bounded admission with the persistent watchdog heartbeat scheduler. The actual selected-class Redis regression grew to 63 messages against cap eight before correction. All 20 scheduler/heartbeat/control cases pass afterward with unchanged limits. | `fec81ca` |
| Legacy capacity baseline cleanup lacked independent verification before candidate measurement | An outer driver verifies exact run-label ownership, including stopped containers, on the same frozen endpoint before advancing. Unknown/late/remnant results fail closed; workload exits and measurement artifacts are preserved. The actual old workflow handoff regression failed before; 11 controls pass afterward. Original baseline contamination was not proved. | `a6c07f7` |
| Beat loaded every worker implementation before starting its scheduler | Use a dedicated producer with copied configuration and equivalent task messages; bounded watchdog behavior and worker registration are retained. All 67 focused controls and fresh full-suite/platform jobs pass. | `6141a719` |
| Raw queue-configuration equality depended on ambient AMQP normalization | Isolate unfinalized configuration and verify effective normalized queues/routes separately. The regression and all 67 focused controls pass, followed by fresh full-suite validation. | `508c7413` |
| Envelope persistence repeated known-absent and known-empty reads | Only successful local creation skips empty child reads; unique winners retain full reads, and snapshots reuse already validated persisted counts. The six-item measured variant removes 24 SQL executions, with policy/feed locks unchanged. Final fresh/replay query controls pass. | `aa92f8e7` |
| Concurrent envelope creation could skip the own-envelope parent-source guard | Revalidate references with the actual winner's envelope ID after reload, before modifying lineage. One real PostgreSQL regression fails before and passes afterward; all 38 new/existing envelope cases pass, including rollback and both matching/conflicting winners. | `aa92f8e7` |
| Successful authentication retired the cache before deferred navigation committed, briefly remounting a blank credential form | Keep session invalidation first and synchronously commit replacement navigation for password and MFA success. The integrated ordinary-login regression fails before and passes after; 50 related auth cases, lint and TypeScript pass, preserving old-cache/identity/lease/late-mutation fences. Earlier browser stalls retain unassigned causes. | `e085733b` |
| Administrator setup accepted emails that the sign-in schema rejects or normalizes differently | Validate and normalize with the actual email schema before database access; reject special-use domains before bootstrap output. Seven new backend cases change from five failed/two passed to seven passed. All sixteen bootstrap tests pass, including 24 reserved-domain and four accepted boundary cases. | `c591c3a8` |
| Administrator seeding accepted empty, oversized or browser-stripped passwords | Admit the actual login credential schema and reject CR/LF before opening a database session. Two boundary failures and three line-break failures reproduce before correction; all sixteen seed-admission controls pass afterward. Transaction, role, reset and concurrent-creation behavior are preserved. | `5a6f9a20` |
| Invalid settings errors printed supplied credentials and keys | Hide input dictionaries in Settings validation errors while preserving actionable rules. Twelve actual rendering regressions fail before; all 96 configuration cases pass after. Defaults, validators and secret derivation remain unchanged. | `5a6f9a20` |
| Native credential-form constraints rejected backend-valid Unicode identities | Use a text input with the email keyboard hint in the shared login/registration fields. Three real-click regressions fail before; 56 related cases pass, including empty-required controls and session-boundary checks. | `a3e782cb` |
| Reserved-domain normalization introduced a Bash 4-only expansion | Use the script's existing printf/tr dependencies for case folding. Bash syntax and all sixteen bootstrap controls pass; Bash 3 runtime testing is unavailable. | `a3e782cb` |
| Administrator local-user creation had the same Unicode email mismatch | Preserve the review/confirmation flow and submit the accepted identity to the user API. Three real-click regressions fail before; all 46 affected cases pass afterward, including empty-required and credential-retention controls. | `5f370197` |
| Production smoke expected the old native email input type after Unicode admission was fixed | Verify the intended text/email keyboard contract, associated label, required credentials, three Unicode admissions and two empty-field rejections in the actual production bundle. Original smoke fails; corrected smoke passes. | `36b2024f` |
| Administrator seeding caught duplicate creation at commit but allowed immediate unique-index flush and audit failures to escape | Move the existing flush/audit into creation recovery, then roll back and reload the locked winner. Four new pure failures reproduce before; all 22 pure cases pass afterward. One PostgreSQL uniqueness-flush case joins the four existing database cases for fresh hosted execution. | `1d057bc1` |

[Structured regression evidence](evidence/2026-10-09-final-review/regression-proof.json)
records pinned environments, before/after failures, hashes and scope limits.
[Login navigation controls](evidence/2026-10-09-final-review/login-navigation-controls.json)
retain the new commit-bound before/after and session-isolation evidence.

The [seed recovery controls](evidence/2026-10-09-final-review/seed-admin-flush-conflict-controls.json)
prove the minimal error-boundary change and preserve all prior test functions.
The [final application source binding](evidence/2026-10-09-final-review/final-seed-recovery-source-binding.json)
retains the earlier tested authentication, administrator, settings and bootstrap
bytes, plus the corrected production smoke. Five PostgreSQL seed cases,
including the real unique-index flush case, remain pending in fresh quality.

The native harness now verifies a rendered, enabled Dashboard time-range
control before declaring login complete. A same-document URL change plus
[document load state](https://playwright.dev/docs/api/class-page#page-wait-for-load-state)
does not establish that the lazy Dashboard rendered. The
[20 controls](evidence/2026-10-09-final-review/native-login-readiness-controls.json)
and [independent adjudication](evidence/2026-10-09-final-review/native-login-readiness-adjudication.json)
verify the readiness gap, unchanged error gates and default bounds. Bounded
passive diagnostics add timestamps, current check, console locations, failed
requests and static responses. The driver intentionally changes; this does
not assign the earlier console failure a cause. All original 33 checks and
resource limits remain intact. The [new source controls](evidence/2026-10-09-final-review/native-ui-1d057bc1-source-controls.json)
verify fourteen application/image groups and exact frozen helpers. Fresh
[native qualification 38074993459](https://github.com/PatrikSi/threatlens/actions/runs/38074993459)
is in progress at this checkpoint and final hosted quality has not yet run.

## Actual native runtime

The earlier local images were freshly built at `e085733b`, with one CPU,
1536 MiB, no swap and one build operation at a time. Both builds pass on their
first attempt; the owned builder and volume are removed. All seven writers stop
before the dedicated migration. Independent inspection verifies twelve services:
eleven running, ten healthy and a completed migration, zero restarts/OOM events,
read-only roots and actual bounded CPU/memory/PIDs without swap. Strict readiness
JSON (`ok: true`) and the checked-in 2.1.0 OpenAPI contract match.
[Exact build/runtime evidence](evidence/2026-10-09-final-review/native-runtime-e085.json)
retains the actual exits, limits, logs, migration and independent readiness check.

| Image | Actual image ID | Runtime user |
|---|---|---|
| Backend | `sha256:2c1bd249517c35540f4accb1273b74437a5e78b7d31b24379b700fc5bbfe3dc5` | `app` |
| Web | `sha256:5793441258dec702b2ff54c25d86dae35b2ef1cfa88e628eee0476f4f273a9a6` | `nginx` |

Fresh HTTP qualification passes **44/44**, all three compensations, dependency/
legal helpers, two non-root startup modes and the bounded network-free actual
backend probe. The probe verifies the changed runtime module hashes, Beat's zero
worker-implementation imports/all 31 schedules and the verified TLS 1.2 floor.
All helper/probe containers are removed. Both exact images pass the selected
HIGH/CRITICAL ignore-unfixed vulnerability and secret policy, with zero findings,
a fresh database, zero original exits and three scanner containers removed.
[Backend evidence](evidence/2026-10-09-final-review/native-backend-e085.json) and
[scan evidence](evidence/2026-10-09-final-review/native-image-scans-e085.json)
preserve the exact artifacts and scoped outcomes. A probe invocation initially
omitted its required image argument and stopped before any workload/container;
a readiness helper initially expected the wrong JSON field. Both helper errors
remain preserved separately from successful actual-image/runtime qualification.

The first browser sequence stops at a stale copied seed assertion before opening
the database or creating a fixture/browser. Its original exit one and all four
compensation checks are retained in the
[pre-creation checkpoint](evidence/2026-10-09-final-review/native-frontend-e085-precreation.json).
A new namespace corrects only that source guard to `e085733b`; actual constant
controls improve from three failures/one pass to four passes, and both independent
reads verify every current source guard. Selectors, actions and limits are unchanged.

The subsequent fresh seed passes; permission transitions pass **seven/seven** and
all six cleanup checks. The first actual full native run on these images executes
**33/33**, with **32 passed / one failed**: Chromium 11/11, Firefox 10/11 and
WebKit 11/11. All six cookie logins pass. The failed Firefox dark article/team
case reaches its second screenshot, which times out at the unchanged 30-second
operation limit. The case's 157.93 seconds include multiple operations and are
not assigned to the screenshot. The first article PNG and a later fallback PNG
exist; the team capture is absent. All generic page/console/API-500/external
listeners report zero events. Thirty-one persisted accessibility surfaces report
zero violations; the failed surface's audit is incomplete. Original exit is one,
and owned browser cleanup reports zero errors/remnants. Source/manifests/drivers
stay unchanged. The
[failed current native checkpoint](evidence/2026-10-09-final-review/native-frontend-e085-checkpoint.json)
retains those counts, exact engine order, image identities, limits and hash-only
capture evidence. The
[read-only capture-source assessment](evidence/2026-10-09-final-review/native-firefox-e085-capture-source.json)
retains the pinned SDK await order and original call-log markers. Screenshot
preparation/evaluation versus scheduling cause is unassigned.

The subsequent single focused Firefox diagnostic preserves the original light
five then dark login/access/article workflows and unchanged limits. It executes
all eight checks: **five passed / three failed**, with both logins passing.
All three primary failures are navigation timeouts; the dark article/team
workflow never reaches the original failed capture. Its driver records 31 stages
and 23 frame events without drops/errors, while whole-container numeric capture
has 47 separate five-second Docker-inspection timeouts. The 114 successful
samples leave a 106.640-second observation gap. Kernel-recorded peak whole-container
memory is 735,498,240 bytes, with no observed OOM/restarts/quota throttling.
A separate point sampled during the run records severe CPU, memory and I/O
pressure; it is not atomic with the browser stages. Neither the diagnostic nor
these observations establish the original screenshot cause or justify an
application/font/timeout change. The
[failed diagnostic](evidence/2026-10-09-final-review/native-firefox-e085-capture-diagnostic.json)
retains separate workload/capture outcomes, exact inputs, bounded stages and
privacy-safe numeric observations.

The first owned-background environment procedure finds seven historical Beat
restarts before any quiet-window or browser execution. Its graceful pause
command succeeds, but the zero-restart guard rejects the state; all six services
restore successfully. Exact current readiness and OpenAPI checks pass afterward.
Retained Beat logs identify six failed Redis heartbeat reads and one stale
heartbeat as watchdog recovery triggers, without assigning their underlying
cause. The seven historical restarts remain recorded. The second procedure
passes the pause guard but fails its declared ten-minute host check: all 40
samples have less than 3 GiB available memory and excessive full I/O pressure.
No browser runs. All six services restore; the exact 12-service stack has
11 running and 10 healthy services, with the original image/resource controls.
The [environment result](evidence/2026-10-09-final-review/native-ui-local-environment.json)
preserves the failed host check and the unused browser budget. A separate
[read-only check on October 10](evidence/2026-10-09-final-review/native-runtime-e085-resumed.json)
confirms current zero restarts/OOM, resource controls, readiness and exact live
OpenAPI without erasing historical failures.

The remaining qualification is moving to a fresh isolated AMD64 runner with
new synthetic credentials/database, the exact original 33-case driver and
unchanged actions, assertions, capture deadlines and container limits.
Qualification-specific helpers remain on the auxiliary review branch.
Their 29 pure fixture/evidence controls pass; they do not count as browser
results. GitHub rejects the first auxiliary workflow before any job runs
because a runner context appears in job-level environment configuration.
The context-only correction starts the
[isolated qualification](https://github.com/PatrikSi/threatlens/actions/runs/38067158775)
at helper revision `3994b4df`; application/image source remains `e085733b`.
That setup passes all 14 command stages, the fresh seed and the exact
12-service runtime snapshot, then its first full readiness request returns
503. No quiet-window or browser execution occurs; owned cleanup passes with
zero remnants. The request arrives 57.235 seconds after the logged Beat start,
before the configured first 60-second heartbeat interval. This is compatible
with a pending worker round trip; the failed component remains unassigned.
The [original setup result](evidence/2026-10-09-final-review/native-ui-isolated-first-setup.json)
and [independent admission assessment](evidence/2026-10-09-final-review/native-ui-readiness-admission.json)
preserve these limits and distinguish actual logged responses from source
semantics.

Helper revision `a57e2fb2` waits for valid startup 503 responses within the
same original 240-second background-start window. Each read uses at most
15 seconds of the remaining window; 200/`ok:true` remains mandatory. Other
statuses, malformed responses and late success fail closed. All 39 helper and
fixture controls pass after this correction. Application timers, browser
contexts, assertions, captures and resource limits remain unchanged. The
[fresh isolated qualification](https://github.com/PatrikSi/threatlens/actions/runs/38068110917)
uses the browser budget left unused by the earlier setup failures. Its actual
terminal result is **six executed / zero passed / six failed / 27 omitted**.
Readiness admits six valid startup 503 responses followed by 200/`ok:true`;
the quiet host gate passes. Each actual login POST returns 422, and four
browser console errors report that status. No page errors, API server errors,
external requests, synthetic resources or cleanup failures occur. The browser
exits 1, with no OOM/restarts, and owned cleanup leaves zero remnants.
The [failed isolated result](evidence/2026-10-09-final-review/native-ui-isolated-invalid-fixture.json)
preserves the actual outcome.

Captured login pages show that the generated `example.test` email is rejected
as a special-use domain. The pinned actual `LoginRequest` schema independently
rejects `.test` and `.invalid` and accepts `example.com`. Validation occurs
before credential authentication or cookie issuance; this failure does not
adjudicate onboarding, authenticated navigation or the earlier Firefox capture.
The [fixture admission proof](evidence/2026-10-09-final-review/native-ui-fixture-auth-adjudication.json)
records six distinct access-log responses, source hashes and real schema
controls. The correction is confined to the auxiliary synthetic email and
request-schema admission before database seeding. Application source and
the original browser driver remain unchanged. A fresh full result is required;
these schema controls are not browser qualification.

The same invalid-input contract also affects production administrator setup.
At `c591c3a8`, seeding applies the actual login email validator and canonical
lowercase identity before opening a database session. Invalid input raises a
fixed actionable error with the original validation details suppressed.
Global settings, derived development keys and existing role/reactivation/
password-reset/concurrent-creation policies remain unchanged. The
[backend regression proof](evidence/2026-10-09-final-review/seed-admin-email-controls.json)
retains unchanged new tests, actual five failures/two passes before and seven
passes after. Four existing PostgreSQL seed cases await fresh full CI.
Bootstrap rejects the six special-use suffixes before creating an environment
file or printing credentials. Its
[sixteen-test proof](evidence/2026-10-09-final-review/bootstrap-email-controls.json)
retains the eighteen previously accepted invalid cases, six already rejected
single-label cases, all 24 corrected cases and four valid suffix boundaries.

Auxiliary workflow `00e6034e` froze application source `c591c3a8`.
Its [source and 44 fixture/evidence controls](evidence/2026-10-09-final-review/native-ui-c591-source-controls.json)
preserve the original driver, 33 checks, contexts, actions/assertions,
captures, resource limits and quiet gate. The
[fresh native qualification](https://github.com/PatrikSi/threatlens/actions/runs/38070210180)
and the single
[uninstrumented capacity comparison](https://github.com/PatrikSi/threatlens/actions/runs/38070244407)
use separate isolated runners. The comparison retains baseline `b73a3363`,
600 seconds per release, five shared start-gate participants, the same target,
20% threshold and independent cleanup. Its actual passing result is recorded
below; the browser result remains failed.

The [actual `c591c3a8` native result](evidence/2026-10-09-final-review/native-ui-c591-publisher-failure.json)
executes **33 checks: 27 pass, six fail, none are omitted**. All six logins pass.
Each article assertion encounters an API 502 reporting the owned publisher's
404; retained publisher logs contain six matching 404 responses. Four console
errors, six API server errors and one WebKit light page error are retained.
The latter reports an `auth/me` access-control failure at path `/`, with no
timestamp; its cause is unassigned and is not attributed to publisher access.
Browser exit is 1, without OOM/restarts; cleanup has zero remnants.

The [independent publisher assessment](evidence/2026-10-09-final-review/native-ui-publisher-access-adjudication.json)
finds that umask 077 makes the requested publisher directory mode 0700.
This is incompatible with the configured nonroot publisher's traversal and
consistent with the actual 404 responses. Actual kernel errno and numeric UID
were not retained. The auxiliary correction explicitly sets only the synthetic
publisher directory to 0755 and its article to 0644, preserving private
ancestors at 0700. A bounded API-container preflight rejects redirects and
requires status 200 with the exact owned content length and SHA before seeding
or browser execution. Both independent and root
[helper controls](evidence/2026-10-09-final-review/native-ui-publisher-correction-controls.json)
pass all 46 cases. The original permission control failed before the fix; its
raw pre-fix log is unavailable, and no log hash is invented.

The [final auxiliary source proof](evidence/2026-10-09-final-review/native-ui-5f370197-source-controls.json)
binds all fourteen image input groups at `5f370197` to workflow `5ccb0d4d`.
The original browser driver, 33 checks, contexts, actions, assertions, capture
timeouts, quiet gate, resource caps and zero-listener-error gates remain
unchanged. The single corrected
[native qualification](https://github.com/PatrikSi/threatlens/actions/runs/38072575289)
is in progress at this committed checkpoint. Actual terminal qualification and
visual review of the captured surfaces are required; pure controls do not
clear it. Temporary qualification helpers and certificate remain confined to
the auxiliary branch.

The final application also validates the full `LoginRequest`, including
password bounds, before administrator seeding and rejects browser-stripped
CR/LF passwords. Its [sixteen admission controls](evidence/2026-10-09-final-review/seed-admin-login-controls.json)
retain the actual boundary and line-break failures before correction.
The seed-only proof's unchanged-settings statement refers to that regression
execution. A separate [Settings correction](evidence/2026-10-09-final-review/settings-error-rendering-controls.json)
adds only `hide_input_in_errors=True`: all field defaults, validators and
derived-key behavior are preserved, with twelve disclosure regressions and
84 existing configuration cases passing. Four existing PostgreSQL seed cases
remain covered by the required fresh full hosted suite.

[Credential-form controls](evidence/2026-10-09-final-review/unicode-login-form-controls.json)
and [administrator user controls](evidence/2026-10-09-final-review/unicode-user-create-form-controls.json)
use real submit-button clicks rather than dispatching submit events directly.
Unicode local parts accepted by the unchanged API schemas reach the existing
confirmation/authentication flows; empty required fields still block
submission. Unicode-domain constraint observations are scoped to jsdom because
native browsers can perform IDN conversion. The administrator controls also
verify the production mutation's exact user-creation payload and eventual
credential-cache cleanup. Its initial after run retains one test assertion
that checked scheduled cleanup too early; waiting for the existing contract
yields all 46 passes, without a production change to cleanup.

### Earlier native candidate `aa92f8e7` — retained

These images were freshly built at `aa92f8e7`, with one CPU, 1536 MiB,
no swap and one build operation at a time. Both builds pass on their first
attempt, and the owned builder and volume are removed. All seven writers stop
before the dedicated migration. Independent inspection verifies twelve services:
eleven running, ten healthy and a completed migration, zero restarts/OOM events,
read-only roots and actual bounded CPU/memory/PIDs without swap. Strict JSON
readiness and equality with the checked-in 2.1.0 OpenAPI contract pass.
The [exact build/runtime record](evidence/2026-10-09-final-review/native-runtime-aa92.json)
retains both original build exits, actual limits, writer shutdown, migration and
the independent readiness check.

| Image | Actual image ID | Runtime user |
|---|---|---|
| Backend | `sha256:8c93f23983fc2baab137db65c9cca92d7dcf689704581dfb587ddb32e0fc89b9` | `app` |
| Web | `sha256:e0061cfce6f1d10fa7c4c68fadb84d2bd8d9805486c638f1de6b3158b4344490` | `nginx` |

That native HTTP qualification passes **44/44**, all three owned compensation
checks, dependency/legal inventory and both non-root startup modes. The bounded,
network-free actual backend probe verifies the two changed runtime source hashes,
the Beat producer's zero worker-implementation imports, all 31 schedule entries
and the unchanged verified TLS 1.2 floor. All helper/probe containers are removed.
Both exact native images pass the established HIGH/CRITICAL, ignore-unfixed
vulnerability and secret policy with zero selected findings, using the same fresh
database in offline scan containers. Their original process exits are zero;
three scanner/download containers are removed with zero errors or remnants.
[HTTP/helper evidence at `aa92f8e7`](evidence/2026-10-09-final-review/native-backend-aa92.json)
and [image scan evidence at `aa92f8e7`](evidence/2026-10-09-final-review/native-image-scans-aa92.json)
retain the exact inputs, original exits, source hashes and scoped cleanup.

The scanner's first current attempt stopped before any download, scan or container
because the pinned tool image was absent locally. Restoration verified the raw
multi-platform index hash and its AMD64 child, then pulled the same immutable pin.
The subsequent scan does not relabel the missing-tool preflight outcome.

Its native permission-transition qualification passes **seven/seven** with
all six cleanup checks. The first policy preflight had stopped before any browser
case because the immutable Playwright image was missing. Its seed was compensated,
and the exact registry index/AMD64 child were verified before restoring the same
pin and using a new fixture. The subsequent first actual native 33-case run
reports **28 passed / one failed / four not executed**: Chromium and Firefox pass all
eleven each, while WebKit passes six and its light-theme login times out. The
remaining four light-theme workflows were not executed. Original browser/launcher
exit is one; the bounded browser container is removed with zero cleanup errors or
remnants. Generic page, console, API-500 and external-request listeners report
zero events. They do not capture lower HTTP statuses or prove successful login.
The screenshot shows an empty login form with SSO availability pending, rather
than onboarding. `/start` is a workspace redirect; accepting it as successful
login would weaken the assertion. Application versus driver/transport cause is
unassigned. Source, manifests and all five driver payloads remain unchanged.
This failed aggregate remains retained with its targeted diagnostic.
The targeted WebKit-only run also fails in the first light context, this time at
submit click before any login request; its dark context passes all six cases.
The observed cold-client delays have no assigned cause. A later network-free
static-form control passes both contexts, with the same immutable browser and
resource limits; it cannot qualify ThreatLens or attribute either earlier failure.
[Focused diagnostic](evidence/2026-10-09-final-review/native-webkit-aa92-diagnostic.json),
[call-log adjudication](evidence/2026-10-09-final-review/native-webkit-aa92-call-log.json)
and [static control](evidence/2026-10-09-final-review/native-webkit-static-control.json)
retain original failures, timing scope and numeric capture.
The [frontend checkpoint at `aa92f8e7`](evidence/2026-10-09-final-review/native-frontend-aa92-checkpoint.json)
preserves the passed permission run, failed native aggregate, original exits,
image/source identities, limits, cleanup and hash-only screenshot evidence.

### Earlier native candidate `6141a719` — retained

The earlier native images are built at `6141a719`, and actual inspection verifies
twelve services: eleven running, ten healthy and a completed migration, with no
restarts. JSON readiness and live contract checks pass. Their image IDs are:

| Image | Actual image ID | Runtime user |
|---|---|---|
| Backend | `sha256:30fac76ff3470d9445f11cb5ee5b6a86e760eb8035069c1eff2b1b8fd3c47d1a` | `app` |
| Web | `sha256:509c66d0360ab6d6a3220473fe4ee67fb39498289c4b69c26f49d75fc12d1368` | `nginx` |

Two unsuccessful aggregate build attempts remain recorded. The host filesystem
was confirmed nearly full, with 6,492,160 free bytes. Removing only about 1.7 GB
of owned reproducible image archives and scanner cache allowed the fresh web-only
third attempt to complete with unchanged source and limits. The backend image
retains its first successful build. Each owned builder cleanup reports zero
remaining containers and volumes. The
[preliminary current runtime record](evidence/2026-10-09-final-review/native-runtime-6141-preliminary.json)
preserves all attempts, image identities, numeric limits and readiness checks.
Fresh native browser qualification passes **33/33 cases** across all three
engines, including 33 Axe surfaces with zero violations, runtime errors or
external requests. Permission-transition workflows pass **seven/seven**, with
all six owned fixture cleanup checks successful. The seed passes, and every
browser container is removed. The
[current frontend record](evidence/2026-10-09-final-review/native-frontend-6141.json)
also preserves the inherited seed-cleanup exit defect: two mocked failure
outcomes exited zero before correction and correctly exit nonzero afterward.
Original cleanup status remains recorded.
[Fresh HTTP/helper evidence](evidence/2026-10-09-final-review/native-backend-6141.json)
records **44/44 HTTP cases**, all three owned compensation checks, dependency/
legal inventory and both non-root startup modes. All four helper containers are
removed, with zero cleanup errors or remnants. A separate bounded, non-root,
network-free backend image probe verifies the selected Beat loader imports zero
of fifteen worker implementations, retains all 31 schedule entries and leaves
the worker unfinalized. It also verifies TLS 1.2, certificate verification and
hostname checking on Python 3.12.13/OpenSSL 3.0.20, without a handshake or task
publication. Its container is removed.
[Current native image scans](evidence/2026-10-09-final-review/native-image-scans-6141.json)
qualify both exact images with zero selected HIGH/CRITICAL vulnerabilities or
secrets, using the same unexpired database in network-free scan containers.
Actual limits are 0.5 CPU, 768 MiB, no swap and 128 PIDs; both containers are
removed without errors or remnants. Three preceding attempts remain failed:
two storage preflights started no scanner, and one evidence guard compared
Trivy's configuration digest with Docker's OCI manifest digest. The corrected
guard verifies the manifest-to-configuration content hashes. Reusing the owned
backend archive through a hardlink avoids duplicate storage; recorded archive
hashes agree before and after scanning. The successful fourth attempt does not
relabel the earlier outcomes. These checks do not clear comparative capacity.

The following completed native qualification is retained for the earlier
`fec81ca` image inputs. It does not qualify the changed Beat producer at `6141a719`.

Both native AMD64 images were built from a fresh tracked Git archive, without
local environment files, the untracked developer lock, backups or dependency
caches. Actual builder inspection confirms one CPU, 1536 MiB, no swap and one
build operation at a time. The owned builder and state volume were removed.

| Image | Actual image ID | Runtime user |
|---|---|---|
| Backend | `sha256:509979ced6388be6d0445479bde66f97c78764d4641c96b1260250c99164533c` | `app` |
| Web | `sha256:9be6b852ae57d1b65350f0d3200d283091e95266b54342ac8818107207a80a7d` | `nginx` |

All writers stopped before the dedicated migration. Actual container inspection
confirms twelve services: eleven running and a completed migration, current
application image IDs, read-only roots, bounded CPU/memory/PIDs and no swap.
API readiness returns HTTP 200; live OpenAPI equals the checked-in 2.1.0
contract. The contract anchor remains
`a608ae21656a8d50f83919e01a16751720c1199a7f4cd552465770bd3df16aaa`.
Private synthetic credentials, fixture identities, cookies, screenshots and raw
logs remain outside the repository. Existing deployment configuration,
`backend/uv.lock`, `backups/` and unrelated services were preserved.

[Native build/runtime evidence](evidence/2026-10-09-final-review/native-runtime.json)
records image IDs, limits, migration/startup and source applicability. Fresh
checks on these actual images are complete:

| Native check | Result |
|---|---|
| [Responsive/browser evidence](evidence/2026-10-09-final-review/native-frontend.json) | First fresh 33/33 UI checks pass, zero Axe/runtime/external events; owned browser containers removed. |
| Permission-transition workflows | Initial six pass/one driver failure is preserved. The active confirmation dialog hid a background Refresh selector; the separately journaled corrected one-case follow-up passes, with all cleanup dispositions successful. Every requested behavior is individually proved; the original failed aggregate is not relabeled. |
| [HTTP and image helpers](evidence/2026-10-09-final-review/native-backend.json) | Exactly one 44-case HTTP run passes; investigation archive, OAuth revocation and session logout cleanup pass. Dependency/legal inventory and both non-root startup modes pass. Four exact helper containers removed, with zero fallback removal/errors/remnants. |
| [Fresh image policy scans](evidence/2026-10-09-final-review/native-image-scans.json) | Both actual image archives pass HIGH/CRITICAL, ignore-unfixed vulnerability and secret scanning with an unexpired database. Zero findings under this policy; scanner limits and offline network mode verified, no owned scan containers remain. |

The HTTP wrapper's preliminary directory-equality assumption failed before any
requests: the retained instance directory was still the Oct8 directory. Both
updated manifests were independently verified against actual running image IDs;
there was no repeated HTTP run. The native policy driver correction adds hidden
lookup only for its deliberate real refetch behind the modal; ordinary operator
selectors, assertions, deadlines and generic error listeners remain unchanged.

## Hosted quality and preserved failures

Fresh final-head quality remains required for the final `5f370197` corrections.
The final review commit must retain exact image-input groups and complete all
fifteen jobs on its actual merge checkout before clearance. Old outcomes below
remain bound to their recorded sources.

The retained [quality run 37914317245](https://github.com/PatrikSi/threatlens/actions/runs/37914317245)
at `aa92f8e7` passes **all fifteen jobs**. Backend reports **4,741 passed / five
skipped** in 1094.38 seconds, overall coverage **87.08%**, reporting coverage
**87.21%**, and all **58 critical module floors**. Capacity smoke, generated
OpenAPI, preview-policy agreement, runtime lock and Python audit pass. Frontend
passes **1,374 tests / 164 files** and its audit. Browsers pass **210 cases**, with
six deliberate split skips and zero retries. AMD64 and ARM64 image smoke/scans
pass. Recovery tooling passes 121 cases with four deliberate opt-in skips;
operations passes 152 with one deliberate opt-in skip. The separate actual
disposable recovery/source-loss drills pass all four plus one cases. Every
quality checkout matches the immutable merge tree, equal to the candidate tree.
Both language analyses and the separate neutral CodeQL aggregate are retained.
The [exact-source full quality record](evidence/2026-10-09-final-review/hosted-quality-aa92.json)
retains all fifteen job/log identities; capacity remains a separately verified
result rather than a conclusion inferred from the quality jobs.

Earlier [quality run 37907945272](https://github.com/PatrikSi/threatlens/actions/runs/37907945272)
at `508c7413` passes **all fifteen jobs**. Backend reports **4,733 passed / five
skipped** in 1172.08 seconds, overall coverage **87.06%**, reporting coverage
**87.21%**, and all **58 critical module floors**. Capacity smoke, generated
OpenAPI, preview-policy agreement, runtime lock and Python audit also pass.
Frontend passes **1,374 tests / 164 files** and its audit; browsers pass **210
cases**, with six deliberate split skips and zero retries. Both platform image
build, scan and stack smoke jobs pass. The
[fresh full quality record](evidence/2026-10-09-final-review/hosted-quality-508.json)
keeps immutable checkout identity, all fifteen job outcomes and log hashes.
Only a Docker-excluded unit test and documentation changed from `6141a719`;
the native images retain their actual `6141a719` labels. This full validation
cleared the earlier backend test failure; comparative capacity was held at that checkpoint.

Earlier [quality run 37903060980](https://github.com/PatrikSi/threatlens/actions/runs/37903060980)
at `6141a719` completes with **fourteen passed jobs and one backend failure**.
All fifteen checkouts match the immutable synthetic merge tree, equal to the
candidate tree. Backend pytest reports **4,732 passed / one failed / five skipped**
in 1234.33 seconds. The only failure is
`test_scheduler_configuration_is_an_independent_copy_of_worker_configuration`:
the raw `task_queues` objects differ after lazy queue normalization. A deterministic control reproduces this lazy normalization mismatch. The
test-only correction compares complete initial configuration and independent
mutable copies in a fresh child, then deliberately normalizes one app before
the other and compares every effective scheduled route. The original control
fails; the corrected control and **67/67 focused Beat cases** pass under the
unchanged one-CPU, 1 GiB and 40 CPU-second bounds. Both independent source
reviews are clear.
[Test isolation evidence](evidence/2026-10-09-final-review/beat-queue-isolation-controls.json)
retains the original failure and all before/after outcomes. The producer,
watchdog and Docker inputs are unchanged; the fresh full hosted run above passes.
This observation does not establish a broken production routing contract. Overall coverage reaches **87.07%** against the
79% floor. The 58 critical module rows are captured, but their strict coverage
gate and subsequent capacity smoke, OpenAPI, lockfile, preview-policy and Python
audit steps are **skipped** after the test failure.

Frontend passes **1,374 tests / 164 files**, lint, build, production smoke and a
zero-vulnerability audit. All browser engines pass **210 cases**, with six
deliberate split skips and zero retry markers. Recovery unit discovery records
**121 passes / four skips**; operations records **152 passes / one skip**.
Separate disposable recovery and source-loss drills pass all **four plus one**
actual cases without skips. Both AMD64 and ARM64 pass running-stack smoke,
startup modes, access-log checks, login, proxy upload and the selected image
vulnerability scans with zero HIGH/CRITICAL findings. Legal/dependency inventory
runs on AMD64 and is deliberately skipped on ARM64. The
[complete current quality record](evidence/2026-10-09-final-review/hosted-quality-6141.json)
keeps all step outcomes, counts, image IDs and log hashes. The earlier ARM64
failure remains a separate result; the fresh pass does not establish its cause.

The original `c4faef4` quality run passed frontend, migrations, OpenSearch, MCP,
recovery tooling and both platform image build/scan/smoke jobs. Backend pytest
reported **4,632 passed / 14 failed / five skipped**: thirteen isolation-mock
failures and one resource-limited IOC child terminated with SIGKILL. WebKit's
provider case exceeded its unchanged 90-second deadline with disabled Save.
The failed run is preserved.

The subsequent `ff9ac24` run passed **1,374 frontend tests / 164 files**, lint,
production build/smoke and the all-severity npm audit with zero vulnerabilities.
All browser engines passed on their first attempt: **147 fixture interactions,
57 standard real-server cases and six separately executed provider/statistics
cases**. The standard invocation's six deliberate split skips are accounted for
by the separate stage, with no browser retry markers.

That run still failed backend pytest with **4,642 passed / 13 failed / five
skipped** at its earlier source. Its IOC resource case passed; this does not
prove the cause of the first SIGKILL. AMD64 failed at the Bake filesystem grant.
ARM64 built its images but Beat remained without a startup scheduler heartbeat,
and Compose reached its unchanged deadline. Beat's scheduler/command/config
source and emulation versions were unchanged from the passing original run.
The bounded-canary integration defect was confirmed independently; it is not
established as the cause of that ARM startup stall.

The full local backend attempt at `c4faef4` exceeded the unchanged 1500-second
deadline and exited 124, with 1510.254 seconds including termination grace.
Owned fixtures were removed; no completed JUnit or current full-coverage result
is claimed for that attempt.

The final `a6c07f7` [hosted quality run](https://github.com/PatrikSi/threatlens/actions/runs/37880697918)
passes all **fifteen workflow jobs**. Backend reports **4,674 passed / five
skipped**, **87.06%** overall coverage, **87.21%** reporting coverage and all
**58 critical module floors** passing. Compile, source-size/complexity, Ruff,
capacity smoke, OpenAPI contract, preview policy, pinned runtime dependency
validation and Python audit pass. Frontend again passes **1,374 tests**, lint,
build and audit. All three browser engines pass **210 cases**, with six
deliberate split skips and no retry markers. Both AMD64 and ARM64 jobs pass
build, running-stack architecture/version/migration/non-root smoke and the
selected vulnerability policy. The final ARM readiness result does not establish
the cause of the earlier stall. MCP, OpenSearch, migration roundtrip and
disposable recovery/restore checks also pass.

[Hosted qualification evidence](evidence/2026-10-09-final-review/hosted-qualification-a6.json)
records exact candidate/merge-tree applicability, per-job log hashes and counts.
Successful analysis workflow jobs remain distinct from the aggregate CodeQL
pull-request check.

The frozen diagnostic-tooling head `b06b364` also completes all fifteen hosted
quality jobs in [run 37885451758](https://github.com/PatrikSi/threatlens/actions/runs/37885451758).
Backend again reports **4,674 passes / five skips**, with **87.06%** overall and
**87.21%** reporting coverage and all 58 critical floors enforced. Frontend,
all three browser engines, both architecture startup/image checks, migration,
MCP, OpenSearch and recovery checks pass. Recovery unit discovery records
121 passes / four opt-in skips; operations discovery records 113 passes / one
opt-in skip. The separate disposable PostgreSQL/source-loss drills exercise
all five omitted cases and pass without skips. This is disposable recovery
simulation, not production fault-domain qualification. Its
[exact-source evidence](evidence/2026-10-09-final-review/hosted-quality-b06.json)
retains log hashes, checkout identity, selected vulnerability-scan scope and the
separate neutral CodeQL aggregate warning.

Fresh [quality run 37893170768](https://github.com/PatrikSi/threatlens/actions/runs/37893170768)
at review head `1251826` completes with **fourteen jobs passed and ARM64 failed**.
All fifteen checkouts match the synthetic merge tree. Backend validation records
**4,690 passes / five skips**, the same coverage percentages and all 58 floors;
frontend records **1,374 passes** with a clean audit; browsers record **210 passes /
six deliberate split skips / zero retries**. Both disposable recovery drills
pass all five actual cases without skips. The
[complete source and outcome record](evidence/2026-10-09-final-review/hosted-quality-125.json)
keeps unit opt-in skips separate from those real drills. ARM64 builds complete
and every service except Beat becomes healthy. Beat emits its early Celery banner
but produces no scheduler heartbeat before the unchanged 240-second watchdog
grace; the original outer startup command exits 124. All eight subsequent ARM
checks/scans are skipped, and cleanup succeeds. Relevant runtime inputs match
the prior successful build, which does not dismiss this failure. A single bounded
ARM startup diagnostic captures import stacks and numeric
resource counters from the frozen `1251826` runtime. Its external, read-only
Celery launcher preserves the original console interpreter, arguments, signals
and exit behavior; the 240-second watchdog grace and resource limits are
unchanged. [Diagnostic controls](evidence/2026-10-09-final-review/arm64-startup-diagnostic-controls.json)
cover twelve launcher cases, missing/failed capture, resource mismatches,
original exit precedence and cleanup errors. The clean operations suite records
151 passes and one deliberate source-loss opt-in skip. The workflow runs only
on its exact auxiliary review branch, without publication or default-branch
changes. These controls validate the diagnostic, not an ARM startup fix.
Only encrypted raw logs are uploaded; plaintext logs are excluded even when
encryption fails. Ten retention fault controls and an exact extracted-workflow
encryption/decryption round-trip pass, with the recipient private key retained
outside the repository.
The single [diagnostic run 37898777870](https://github.com/PatrikSi/threatlens/actions/runs/37898777870)
at diagnostic source `e5fd9e3` completes successfully against frozen runtime
`1251826`. Its [verified retained outcome](evidence/2026-10-09-final-review/arm64-startup-diagnostic-outcome.json)
records original startup exit zero, all fourteen independent capture checks,
exact decrypted-log hash consistency and zero owned cleanup remnants. Four
stack dumps show SQLAlchemy and Pydantic initialization in worker task imports,
then the normal scheduler loop. The early banner and service start occur about
47.962 and 152.239 seconds after arming. Initial sampled whole-container CPU
usage is near its half-core limit, with throttled periods; no OOM or restart is
recorded. Those samples have a separate clock and do not prove the cause of the
earlier failure. A private parser's initial zero Compose state counts were
corrected for the owned container's two-space separator; the original
projection is retained. This instrumented startup pass does not clear the
failed uninstrumented ARM quality run.
The [runtime source attestation](evidence/2026-10-09-final-review/native-runtime-applicability-125.json)
also confirms all 1,453 scoped paths, modes and blobs match the qualified native
runtime; images keep their actual build revision.

## Beat startup dependency boundary

The captured startup exposes work the scheduler does not need: loading the
fifteen worker implementation modules before it can publish scheduled task
names. Beat now selects a separate producer app with an independent copy of
the worker configuration and no implementation imports. Worker registration
is unchanged. The producer explicitly preserves ignored-result defaults and
per-entry overrides, routing, JSON messages, schedule options and the selected
bounded canary/heartbeat scheduler. Watchdog grace, resource limits and
publication cadence remain unchanged.

The [regression and parity proof](evidence/2026-10-09-final-review/beat-producer-controls.json)
records a failing original fresh-process loader guard and **67 passing cases**
after correction: all ordinary and optional AI schedule messages, explicit
metadata/ETA/countdown/result overrides, configuration isolation and existing
watchdog/scheduler controls. No task body, Redis service or provider executes
in that window. Diagnostic compatibility retains both the frozen original and
new exact app targets: two controls fail before that update, all thirteen pass
afterward, and the clean operations suite records **152 passes / one deliberate
opt-in skip**. Both independent source reviews are clear. This is a supported
startup dependency improvement, not a proved cause of the earlier unframed
failure. Fresh native, complete hosted quality and uninstrumented capacity
qualification are required for this changed runtime before release clearance.
The [independent candidate source review](evidence/2026-10-09-final-review/beat-producer-source-applicability-6141.json)
pins the producer, selected watchdog and parity-test hashes while retaining
unchanged worker, scheduler, dependency, Docker and Compose objects. Its source
review status is separate from the failed full-suite result above.

## Security finding review

The recorded earlier CodeQL runs completed with **25 Python and one JavaScript
high alerts**. Final-head analyses and aggregate outcome remain part of the
fresh quality gate; their actual result is recorded on the PR. The
[per-flow review](2026-10-09-codeql-triage.md) documents the SMTP policy gap
corrected in this pass and the constrained application/test-fixture findings.
All 26 results remain reported by the latest analyzer, including SMTP. Independent
source-flow review supports fifteen false-positive dispositions and one test-fixture
disposition for the sixteen alerts annotated as new. Actual API readbacks verify
those dispositions; the other ten existing alerts are unchanged. The aggregate
check changed from failure to **neutral**, retaining a warning about the
different caller-derived analysis configuration identifiers on main and the PR.
Both language analyses completed over the full source trees. The
[audit](evidence/2026-10-09-final-review/codeql-dispositions.json) preserves the
original failure and specific reopening assumptions. No query, severity
threshold or source exclusion changed. This is not a zero-alert scan claim. Fresh
native image vulnerability and secret scans retain the established
HIGH/CRITICAL, ignore-unfixed policy.

The fresh `1251826` analyses retain the same 26 findings and introduce no observer
flow. Its aggregate is neutral with zero annotations and the same configuration
warning. The current PR scope has ten open alerts, matching the earlier snapshot;
the default-main scope has eleven. The extra default-main alert belongs to an
older configuration whose updated PR flow was already reviewed. These are
different ref scopes, not an additional disposition or a zero-alert result.
Fresh `6141a719` analyses also retain **25 Python and one JavaScript results**,
with no result located in the new producer or changed watchdog file. The separate
aggregate remains **neutral**, with zero annotations and the historical
configuration warning. The current PR scope remains ten open alerts and the
default-main listing eleven; no further dispositions, query changes or severity
changes were made. Exact analysis IDs and SARIF hashes are retained in the
current quality record.

Retained `aa92f8e7` analyses contain **25 Python and one JavaScript results**:
Python analysis `1922341461` and JavaScript analysis `1922335253`. No result or
code-flow location points to either changed envelope/lineage runtime file.
The aggregate remains neutral with zero annotations and the historical
configuration warning; PR/main open-alert scopes remain ten/eleven. Exact
analysis and SARIF identities are in the
[earlier full quality record](evidence/2026-10-09-final-review/hosted-quality-aa92.json).

## Capacity comparison

The single fresh uninstrumented
[pair 38070244407](https://github.com/PatrikSi/threatlens/actions/runs/38070244407)
compares actual candidate `c591c3a8` against matched-harness baseline `b73a3363`
on workflow `00e6034e`. It **passes** the unchanged 20% comparator and both
600-second absolute-budget checks. Actual inputs, source labels, five shared
start-gate participants, target and fingerprints match the frozen plan.
Required successful-export/AI/governance/DNS/header counts are
286/301/300/6/6 for baseline and 292/301/300/6/6 for candidate. Each independent
cleanup verifies sixteen probes and zero remnants. No diagnostics were enabled.
The artifact's advertised digest, all nine regular bounded archive members,
measurement hashes and Bash `-e` exit evidence reconcile. Replaying the frozen
default-threshold comparator produces the exact hosted comparison with exit 0.
The [actual capacity record](evidence/2026-10-09-final-review/capacity-c591-600s-hosted-evidence.json)
and [independent capture review](evidence/2026-10-09-final-review/capacity-c591-independent-capture-review.json)
preserve this proof; no repeated identical pair or threshold waiver occurred.

| Metric | Baseline | Candidate `c591c3a8` | Change |
|---|---:|---:|---:|
| Processing-dispatch queue p95 | 189.608 ms | 188.614 ms | −0.524% |
| Feed task p95 | 751.306 ms | 475.815 ms | −36.668% |
| Governance p95 | 66.937 ms | 68.939 ms | +2.991% |
| Sampled waiting-query age peak | 556.004 ms | 189.479 ms | −65.921% |

Three optional series remain insufficient: asynchronous export, its success
subset and export policy conflicts. No required series is insufficient.
Application limits use CPU affinity and a sampled owned-process RSS watchdog;
an application hard cgroup or no-swap cap is not claimed. This one sequential
pair provides no estimate of run-to-run variance or causal speedup.

The [explicit measured-path source assessment](evidence/2026-10-09-final-review/capacity-5f370197-measured-path-applicability.json)
binds this result to the unchanged workload at `5f370197`. Exactly four backend
paths differ: the administrator seed module, Settings and their two unit
modules. The capacity harness does not launch administrator seeding or select
those unit tests. The complete seed database transaction AST matches. The
complete Settings module AST matches after removing only the error-rendering
flag, and three isolated valid Settings cases produce equal outputs, fields
and JSON schemas under the exact locked Pydantic versions. All other 1,285
backend objects and five capacity operation/workflow groups match.
This is measured-path applicability, with actual candidate `c591c3a8` retained;
the entire backend trees differ. Fresh full-suite and native qualification
cover the new setup, invalid-input and frontend paths independently.

### Earlier passing capacity candidate `aa92f8e7` — retained

The single predeclared uninstrumented
[earlier pair 37914335157](https://github.com/PatrikSi/threatlens/actions/runs/37914335157)
compares matched-harness baseline `b73a3363` with the measured candidate
`aa92f8e7`, using each ref's locked dependencies. It **passes the unchanged
20% regression gate**. Both 600-second workloads pass absolute budgets,
with zero task/sampler errors, identical comparison fingerprints and all required
samples. Source/target labels, five-lane arrival contract and resource limits
match the frozen plan. Both independent cleanup attestations pass sixteen probes
with zero errors or remnants; the successful sequential Bash `-e` step and frozen
exit-preserving wrapper verify both workload exits. The artifact was downloaded
once, with no redispatch or repeated identical pair.
The [verified earlier capacity record](evidence/2026-10-09-final-review/hosted-capacity-aa92.json)
retains all artifact hashes, measurements, comparison, process/cleanup proofs
and a deterministic replay of the exact comparator with an equal result.
The later web-only `e085733b` correction preserves the entire backend tree,
five qualification-control files and all three matched runtime harness blobs.
Its [source consistency proof](evidence/2026-10-09-final-review/release-consistency-e085.json)
keeps the actual tested capacity head/candidate at `aa92f8e7`, separately from
new web/native/final-head quality. No new capacity pair is needed for unchanged
measurement inputs.
The [predeclared plan](evidence/2026-10-09-final-review/capacity-plan-aa92.json)
records the original qualification intent and unchanged workload/deadlines.
Actual application supervision pins one-core affinity and samples the owned
process-tree RSS against a 1 GiB ceiling with an 840-second watchdog. PostgreSQL
uses 0.5 CPU/512 MiB and Redis 0.5 CPU/128 MiB; both Docker fixtures enforce no
swap. The immutable plan's application `swap:false` was predeclared but is not
enforced or attested by this process supervisor. Application/host swap policy
is unobserved; it is not claimed as a verified capacity control. Native image,
builder, runtime and scanner no-swap controls are separately verified.

| Metric | Baseline | Candidate | Change |
|---|---:|---:|---:|
| Processing-dispatch queue p95 | 178.850 ms | 167.452 ms | −6.373% |
| Feed task p95 | 778.198 ms | 468.734 ms | −39.767% |
| Governance p95 | 115.699 ms | 103.397 ms | −10.633% |
| Sampled waiting-query age peak | 456.502 ms | 205.363 ms | −55.014% |

The full comparison retains every metric and three optional series without
enough samples: asynchronous export, its success subset and export policy
conflicts. No required series is insufficient. Baseline logs contain 156
successful and eight attention processing returns; candidate logs contain 155
successful returns. These counts are preserved rather than replaced with a
blanket all-task-success claim. Each ref completes 300 governance operations;
AI successes are 301, while exports succeed/conflict 294/7 versus 296/5.
This one sequential pair qualifies the configured GitHub reference gate. It
does not estimate run-to-run variance, qualify intended deployment hardware or
establish why earlier comparisons failed. `Conclusive` denotes required sample
coverage. The observed lower metrics do not establish a causal speedup from the
read reduction. Earlier failed comparisons remain failed under their own inputs.

### Earlier capacity outcomes and diagnosis — retained

The earlier uninstrumented
[release pair 37903114128](https://github.com/PatrikSi/threatlens/actions/runs/37903114128)
compares matched-harness baseline `b73a3363` with new application candidate
`6141a719`, using each reference's locked dependencies. Both 600-second workloads
pass absolute budgets, required sample coverage and independent cleanup, with
zero task/sampler errors and zero owned remnants. The comparator remains
**failed**: processing-dispatch queue p95 is **160.009 → 193.713 ms (+21.064%)**,
and feed-task p95 is **590.117 → 723.382 ms (+22.583%)**. The sampled query-age
peak is **610.478 → 219.283 ms (−64.08%)**; that earlier peak failure does not
recur in this pair, which does not retrospectively clear the previous result.
All source labels, inputs, shared-arrival identities, fingerprints, resource
limits and the unchanged 20% threshold are verified. The
[current result record](evidence/2026-10-09-final-review/hosted-capacity-6141.json)
retains the actual failed comparison and every metric. Here, `conclusive` means
required samples exist; it does not establish statistical confidence or cause.
A single bounded PostgreSQL/Redis six-item feed profile now passes, followed by
an identical-body replay. The
[phase record](evidence/2026-10-09-final-review/feed-six-item-profile-6141.json)
retains five recorder controls, one actual profile, source hashes and cleanup.
First ingestion persists six items, events and envelopes with **233 database
executions**; replay adds none and uses **16**. Ten synthetic callback visibility
queries are excluded from persistence counts. Callback-excluded descriptive
wall/thread CPU time is **163.487/106.133 ms**; the nested lineage stage accounts
for **132 queries and 63.546/42.035 ms**. Nested timings overlap and must not be
added. The two parser calls total only **3.365/2.845 ms**.
Twelve locked envelope lookups confirm a repeated known-absent lookup for every
new envelope. The narrow measured correction removes **24 SQL executions
(233 → 209)** while retaining policy/feed locks, unique-conflict reloads,
persisted lineage validation and atomic commits. A later reload-only reference
guard also closes a reproduced own-envelope parent race; final fresh/replay
query controls retain the reduced normal read budget. The
[combined controls](evidence/2026-10-09-final-review/envelope-read-reduction-controls.json)
preserve the four original budget/path control failures, the separate semantic
race failure and all **38 final passes**. The 209-query profile applies to its
recorded earlier measured variant: instrumented wall/CPU time rises from
163.487/106.133 to 196.295/109.686 ms. This establishes eliminated reads,
without an end-to-end speedup claim for either source.
The profile does not establish either hosted regression's cause. Both owned
fixture containers and their two anonymous volumes are removed, with zero row,
Redis-key, cleanup error or remnant counts. No threshold change is claimed.

The first private evidence predicate expected a nonexistent raw process-exit
field and list-shaped budget violations. A retained-artifact-only projection
correction uses the actual empty budget dictionary and typed error arrays, with
zero workload exits inferred narrowly from the unique successful sequential
measurement step and the exact exit-preserving wrapper under Bash `-e`.
The [projection controls](evidence/2026-10-09-final-review/capacity-capture-projection-controls.json)
record one failing actual-schema control before correction and 44 passing
controls afterward, including exact wrapper return propagation. Original
capture evidence is retained; no artifact was downloaded again and the actual
comparator failure is unchanged.

The first hosted reference comparison ran sequential **600-second** workloads
at exact baseline `35ac120b28820b6e5b1222a50b5f70f7a6b85c3b` and candidate
`c4faef4edc2074f4e1ef961c7e3116637c3f4f96`, each with its own locked
requirements. Both absolute workloads passed, but the unchanged **20%**
comparison failed for classification queue wait (**+24.908%**) and processing
dispatch queue wait (**+84.762%**). No required samples were missing.

Offered workload counts were equal; actual durable processing executions rose
from 354 to 710, including attention outcomes from 48 to 355. Dispatcher,
article pipeline and measurement source were identical between those refs.
The subsequent focused regression confirms an early-input recovery defect,
but the original logs do not identify every attention stage/reason, so this
report does not attribute all extra work or latency to that defect. Baseline
cleanup lacked independent attestation; contamination was not demonstrated.

The new exact-source comparison at `a6c07f7` also **fails** the unchanged 20%
threshold: governance p95 **75.556 → 113.892 ms (+50.739%)**, and sampled
lock-waiting query age peak **148.032 → 189.491 ms (+28.007%)**. Both absolute
workloads pass, all required samples exist, and both independent cleanup
attestations pass with zero errors or remnants. Offered workloads remain equal:
480 new articles and 300 operations per service over 600 seconds per ref.
The previous queue-wait flags pass in this pair; actual durable processing
executions fall from 217 to 162, with attention outcomes from 48 to zero.

The exercised governance mutation and conflicting AI/export/lineage policy
fences are unchanged from baseline. AI intentionally holds a shared policy
fence through the synthetic 100 ms provider call. Governance median stays
near 8 ms, but existing aggregate artifacts lack operation timing and waiter/
blocker identities needed to explain its higher tail. The sampled lock metric
is query age while waiting, not actual lock-wait duration. Neither finding is
dismissed as noise; a bounded diagnostic capture was then prepared and executed
without changing workloads, authorization fences, budgets or comparison thresholds.

The opt-in observer lives outside the backend/web build contexts and is supplied
identically to both untouched frozen references. Twenty isolated observer
controls and 23 wrapper/cleanup controls pass. The separately journaled
[ten-second local integration probe](evidence/2026-10-09-final-review/diagnostic-local-validation.json)
passes with all five governance operations captured, zero errors/drops and
clean owned-resource teardown. Its recorded hooks total approximately 151 ms
over about thirteen seconds of sampling. This validates injection and capture;
it does not clear the sustained capacity gate. The observer records its own
hash/overhead and bounded operation, query-category, waiter/blocker-alias and
host counters. Original timers and result artifacts remain authoritative;
observed transaction-end requests are not exact lock-release times.

Diagnostic tooling is committed as `2993227`; its
[source-object proof](evidence/2026-10-09-final-review/diagnostic-source-applicability.json)
confirms unchanged application/image inputs. A clean Recovery-job environment
initially failed seven new plugin controls because it installed only cryptography.
The job now derives the exact declared pytest/SQLAlchemy pins and uses runtime
constraints for their dependency closure. The fresh, twelve-package environment reports **113 passes, one deliberate
source-loss opt-in skip and zero errors**, and `pip check` passes. The hosted
recovery drill separately exercises that source-loss reconstruction case. The
[before/after proof](evidence/2026-10-09-final-review/diagnostic-clean-dependencies.json)
retains both outcomes and the existing job deadline.

Two follow-up diagnostic contracts are fixed locally. Captured, recovered
workload query failures retain type/count summaries without marking capture as
failed; observer faults still fail. Four new regression controls failed before
and all 24 observer controls pass afterward. Standalone verification now also
requires a passing measurement and integer-zero original workload exit; four
negative controls failed before correction. The operations suite reports
119 passes and one deliberate opt-in skip afterward. The
[query-outcome proof](evidence/2026-10-09-final-review/diagnostic-query-outcomes.json)
and [workload-verification proof](evidence/2026-10-09-final-review/diagnostic-workload-verification.json)
preserve the results. Neither correction changes the frozen observer used by
the completed frozen hosted diagnostic or the application workload.

Each pair has matching fingerprints internally, but the first two uninstrumented
hosted pairs used different CPU models. Cross-run absolute improvements cannot
establish the effect of a fix. Both are GitHub-hosted reference comparisons, not qualification
of an operator's intended hardware. Earlier failures remain recorded. The
[first comparison](evidence/2026-10-09-final-review/hosted-capacity-c4fa.json) and
[second uninstrumented pair](evidence/2026-10-09-final-review/hosted-capacity-a6.json)
preserve exact sources, unchanged budgets, offered/completed work and flagged
measurements.

The single opt-in diagnostic pair
[run 37885492555](https://github.com/PatrikSi/threatlens/actions/runs/37885492555)
completed both unchanged 600-second workloads and their absolute budgets.
Each capture contains all 300 governance operations and five unique lanes,
zero query-execution/capture errors or drops, and verified owned-resource
cleanup. The unchanged comparator still fails: `export:succeeded` p95 rises
233.431 to 481.511 ms (+106.276%), and alert-evaluation task p95 rises 29.261
to 44.890 ms (+53.412%). Overall export p95 falls 516.351 to 481.511 ms;
baseline had nine slow policy-conflict outcomes, while candidate had none,
so success-only samples cover different outcome populations. Governance p95
falls 109.704 to 53.800 ms and sampled lock-query-age peak changes +4.847%.
These observations do not clear the original failed pair. The
[diagnostic evidence](evidence/2026-10-09-final-review/hosted-capacity-diagnostic-b06.json)
preserves its failure and the frozen observer identity.

The trace exposes a measurement defect: each paced lane starts an independent
monotonic epoch on executor entry. Baseline lane entry spread is 2.890 ms;
candidate spread is 304.392 ms, despite equal nominal workload fingerprints.
Same-index governance starts about 500.660 versus 804.061 ms after export,
changing actual overlap. The [independent phase analysis](evidence/2026-10-09-final-review/diagnostic-phase-analysis.json)
records numeric timings, host counters and source-object checks. A bounded
five-lane readiness barrier with a shared epoch is corrected in `7900143`
under the new `five-lanes-shared-monotonic-epoch-v2` arrival contract. A fresh,
matched harness baseline retains the original application; prior failures remain recorded. The budget,
600-second duration, nominal offsets and 20% threshold remain unchanged.

The [regression proof](evidence/2026-10-09-final-review/sustained-shared-start-regression.json)
records the actual delayed-entry phase failure before correction and 41 passing
controls afterward. Both independent reviews found no blocker. The
[single ten-second PostgreSQL/Redis validation](evidence/2026-10-09-final-review/shared-epoch-local-validation.json)
passes with five operations per service, complete capture, no budget violations
and clean teardown; median actual AI/governance offsets from export are
250.001/500.019 ms. Scheduling can still delay operations after the shared epoch.

The actual matched baseline commit is
`b73a3363fc0cb7354e36d3f92b2394448a459d02`, derived from original application
`35ac120b28820b6e5b1222a50b5f70f7a6b85c3b`. Only three measurement files
are backported; their blobs match candidate
`79001439677cae246faef0a398ddea50c47faf80`. The
[source attestation](evidence/2026-10-09-final-review/shared-epoch-source-applicability.json)
checks unchanged application, migrations, dependencies, helpers and budgets.
The [native applicability proof](evidence/2026-10-09-final-review/native-shared-epoch-applicability.json)
confirms that the five candidate changes are Docker-excluded tests and all
runtime/image inputs still match the qualified native build. The predeclared,
uninstrumented 600-second
[matched baseline/candidate pair](https://github.com/PatrikSi/threatlens/actions/runs/37888628612)
and [unchanged-candidate control pair](https://github.com/PatrikSi/threatlens/actions/runs/37888630228)
have completed with the same caps and 20% threshold. The release pair passes
both absolute budgets, sample completeness and independent cleanup, and all
latency p95 comparisons stay within 20%. Its sole relative failure is the
maximum sampled query age while Lock-waiting: **220.296 → 437.157 ms (+98.441%)**.
That metric measures query age at the sampling instant, not elapsed lock-wait
duration. The unchanged-source control passes, including **234.239 → 120.379 ms**
for the same peak. These pairs ran on different hosted machines; their absolute
values cannot establish a causal cross-host comparison or dismiss the failure.
The [source, result and cleanup evidence](evidence/2026-10-09-final-review/hosted-shared-epoch-pairs.json)
retains both complete outcomes and the earlier failed pairs. The release capacity
gate remained failed at that checkpoint.

The version 2 observer retains bounded numeric snapshots of the original sampler
without consuming its cursor result, and records later waiter/blocker context
when a new maximum appears. Its independent verifier reconstructs sample counts,
waiting-row totals, concurrent-session maximum and the exact rounded query-age
peak from retained arrays. The [observer controls](evidence/2026-10-09-final-review/diagnostic-v2-observer-controls.json)
pass all 34 cases, and the [independent verifier controls](evidence/2026-10-09-final-review/diagnostic-v2-verifier-controls.json)
pass all 24 selected methods; clean operations validation runs 140 cases with
139 passed and one deliberate source-loss opt-in skip. The single
[real ten-second integration probe](evidence/2026-10-09-final-review/diagnostic-v2-local-validation.json)
passes with 537 original samples, five governance operations, all five lanes,
zero capture errors or drops, strict verifier success and no cleanup remnants.
This validates diagnostic correctness rather than sustained performance.

The single predeclared [600-second matched diagnostic pair](https://github.com/PatrikSi/threatlens/actions/runs/37893213636)
at workflow head `1251826` passes the unchanged comparator with no regression
flags. Its [complete outcome and sampled peak context](evidence/2026-10-09-final-review/hosted-capacity-v2-diagnostic-125.json)
verifies both frozen source refs, the identical observer, original sampler
reconciliation, 300 governance operations per ref, zero capture errors/drops and
two independent cleanups with fifteen probes each and zero remnants. Query-age
peaks decrease **606.404 → 501.316 ms**; governance p95 is **70.041 → 82.903 ms
(+18.364%)**, within the 20% threshold. Both maximum snapshots occur during the
first governance operation. Later graph context shows an exclusive policy waiter
blocked behind an alias with an observed shared policy fence; background task
identity is unassigned, and the graph is not an atomic witness of the original
row. No new causal application defect is proved. Instrumentation perturbs timing,
so this passing diagnostic does not clear the earlier uninstrumented failure.
No retries, threshold changes, fence weakening or model promotion occurred.

The [independent source review](evidence/2026-10-09-final-review/capacity-v2-source-review.json)
confirms that all feed item upserts already precede event emission. Work under
the shared policy fence persists event snapshots/provenance and verifies lease
ownership before the atomic commit. Its source shape is consistent with the
observed background acquisitions, but does not identify that task or establish
the cause of the failed comparative peak. No authorization or transaction-order
change is justified by this evidence.

The [phase and host projection](evidence/2026-10-09-final-review/capacity-v2-phase-host.json)
retains actual service separation, operation coverage and sampled host counters
for the same diagnostic pair. Its shared-epoch estimate is an inferred upper
bound; observer elapsed sums exclude nested snapshot work and cannot be
subtracted as workload CPU costs. Neither sampled counters nor service overlap
establish a blocking task or dismiss the failed release comparison.

## Feature completion and release boundary

The corrected gaps concern implemented workflows: report/editor draft rights,
investigation notes, provider configuration, publication pagination,
scheduling, processing recovery and Beat admission. No additional unexpected
production stub was identified in the reviewed shipped scope. The documented
2.1.0 surface includes team intelligence/review lineage, automation delivery,
AI governance/qualification and read-only delegated MCP access.

The [release boundaries](../releases/2.1.0.md#qualification-and-boundaries)
remain explicit: installation-wide tenant isolation, MCP writes/SSE/prompts/
resources/dynamic registration, universal vendor-native push, external hunt
reminder delivery and the old article-chatbot proposal are outside this
release. Chosen-model semantic promotion still needs representative captured
outputs and named human review of the twelve prepared cases. Provider-contract
and configuration tests do not complete that approval; ordinary ingestion and
classification do not require model promotion.

The remaining current gates at this committed checkpoint are fresh native
qualification at `1d057bc1` and hosted quality on the final review head.
Capacity passes at actual measured candidate `c591c3a8`, with explicit
measured-path applicability to `1d057bc1`. The PR records the actual terminal
results without adding an evidence-only commit that changes the tested head.
Earlier `e085733b` cookie
logins, both exact local images and backend/runtime checks pass. Earlier failures
remain recorded under their actual runtime and harness inputs; they are not
assigned causes by later passing observations.
The CodeQL neutral baseline-configuration warning and chosen-model
promotion limits remain explicit; no merge, tag or publication has occurred.

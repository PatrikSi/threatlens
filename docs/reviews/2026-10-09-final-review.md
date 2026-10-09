# Iterative code review and qualification — 2026-10-09

ThreatLens **2.1.0** is prepared, and the rebuilt synthetic local instance is
running at **http://127.0.0.1:3001**. The frozen application/image revision is
`fec81cabf6cdcabca8111ed2c78ac8c4e895c433`; the qualification candidate is
`a6c07f7f2718cd4f9a6caab823b481fc2ac7525e`. Their backend, web, Docker,
Compose, license and version Git objects are identical. Images retain their
actual build revision. All corrections were committed as `Patrik <patrik@local>`.

**Decision at this checkpoint: source fixes are complete; final native,
hosted quality/platform and comparative capacity qualification is underway.**
Version 2.1.0 is already aligned across the version file, frontend metadata,
OpenAPI and deployment defaults. These pre-merge corrections need no further
version bump. [Draft PR #31](https://github.com/PatrikSi/threatlens/pull/31)
contains the review candidate; remote main, release tags, published images and
model promotion remain unchanged.

This follows the [Oct8 review](2026-10-08-final-local-review.md), preserving its
original sources, failures and qualification limits.

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

[Structured regression evidence](evidence/2026-10-09-final-review/regression-proof.json)
records pinned environments, before/after failures, hashes and scope limits.

## Actual native runtime

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
records image IDs, limits, migration/startup and source applicability. Live
browser/HTTP checks, dependency/legal inventory and fresh vulnerability/secret
scans are separate gates and are still running at this checkpoint.

## Hosted quality and preserved failures

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
is claimed for that attempt. Final hosted backend/coverage/contract/audit and
platform qualification at `a6c07f7` are underway.

## Security finding review

CodeQL completed with **25 Python and one JavaScript high alerts**. The
[per-flow review](2026-10-09-codeql-triage.md) documents the SMTP policy gap
corrected in this pass and the constrained application/test-fixture findings.
All 26 alerts remain reported by the subsequent analyzer, including SMTP;
none were dismissed or suppressed. This is not a zero-alert scan claim. Fresh
native image vulnerability and secret scans retain the established
HIGH/CRITICAL, ignore-unfixed policy.

## Capacity comparison

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

A new exact-source comparison at `a6c07f7` is underway after the production
repair and independent cleanup gate. Duration, offered workloads, limits,
measurement contract and comparator remain unchanged. This uses a fresh
GitHub-hosted reference VM; it is not qualification of an operator's intended
hardware. Earlier failed shared-host and hosted comparisons remain recorded.

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

The final release decision must incorporate current native browser/HTTP/image
results, the complete hosted backend/coverage/platform gates and the unchanged
comparative capacity result. Pending gates are recorded here until their
actual results are available.

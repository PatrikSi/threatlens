# Iterative code review and qualification — 2026-10-09

ThreatLens **2.1.0** is prepared, and the rebuilt synthetic local instance is
running at **http://127.0.0.1:3001**. The frozen application/image revision is
`fec81cabf6cdcabca8111ed2c78ac8c4e895c433`; the qualification candidate is
`a6c07f7f2718cd4f9a6caab823b481fc2ac7525e`. Their backend, web, Docker,
Compose, license and version Git objects are identical. Images retain their
actual build revision. All corrections were committed as `Patrik <patrik@local>`.

**Decision at this checkpoint: native qualification and all fifteen hosted quality
jobs pass; comparative capacity still fails.** Reviewed CodeQL dispositions
cleared its failure, leaving a neutral check with a baseline-configuration warning.
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

## Security finding review

CodeQL completed with **25 Python and one JavaScript high alerts**. The
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
dismissed as noise; a bounded diagnostic capture is being prepared without
changing workloads, authorization fences, budgets or comparison thresholds.

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
the running hosted diagnostic or the application workload.

Each pair has matching fingerprints internally, but the two hosted pairs used
different CPU models. Cross-run absolute improvements cannot establish the
effect of a fix. Both are GitHub-hosted reference comparisons, not qualification
of an operator's intended hardware. Earlier failures remain recorded. The
[first comparison](evidence/2026-10-09-final-review/hosted-capacity-c4fa.json) and
[final planned pair](evidence/2026-10-09-final-review/hosted-capacity-a6.json)
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
changing actual overlap. A bounded five-lane readiness barrier with a shared
epoch is being corrected under a new arrival contract. This requires a fresh,
matched harness baseline; the prior failures remain recorded. The budget,
600-second duration, nominal offsets and 20% threshold remain unchanged.

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

Comparative capacity remains the unresolved release gate. The completed native,
hosted backend/coverage, architecture and browser results apply to the frozen
candidate. The CodeQL neutral baseline-configuration warning and chosen-model
promotion limits remain explicit; no merge, tag or publication has occurred.

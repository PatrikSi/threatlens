# Code review remediation — 2026-10-08

This follows the [comprehensive review](2026-10-08-code-review.md). The source
candidate is `65f4a755c4899373dba14b8128ac25575b130cb6`, with application version
**2.1.0** and the [reviewed upgrade notes](../releases/2.1.0.md). Changes are
committed incrementally as `Patrik <patrik@local>`.

The identified implementation findings are fixed and the 2.1.0 source candidate
is prepared. The first sustained pair passes its absolute budgets, but the
unchanged comparator flags successful-export p95 latency. An independent pair
in reverse order is still running; capacity qualification remains open.
No public tag, image promotion, remote merge or model promotion has been made.

## Changes completed

| Finding | Correction | Commit |
|---|---|---|
| R01: frontend dependency audit | Upgraded Tailwind and its PostCSS integration to 4.3.3, removed the vulnerable Tailwind 3 dependency chains, and retained the established palette, spacing, rings, radii, shadows, themes and native form backgrounds. Renamed affected outline utilities. No advisory suppression or audit-policy change. | `f833e7b` |
| R02: inconsistent corpus approval | Evaluation now shares the promotion gate's exact case-digest and named, timezone-aware, non-future review checks. Stale or blank provenance cannot satisfy `--require-reviewed` or the approval summary. | `a858059` |
| R02: silently ignored CLI gate | `--prepare` and `--gate` are mutually exclusive; thresholds require an actual gate and prediction input cannot be combined with preparation. | `a858059` |
| R02: incomplete review handoff | Added an offline package generator with twelve case sheets, exact source/annotation digests, model-output templates, per-claim review forms, coverage gaps and promotion thresholds. Supplied captured outputs are retained byte-for-byte; existing review directories are protected. | `2c095da` |
| R03: runtime identity used for migration | README operational examples now stop all writers and run the dedicated migration service. The restricted API identity cannot perform DDL. | `ce8be34` |
| R03: candidate metadata and upgrade documentation | Synchronized every version field, regenerated the API contract and documented migrations 0106–0125, the coordinated worker upgrade, downgrade guards, article-text mechanisms and feature boundaries. | `d4b79ad` |
| Build context privacy | Excluded local environment variants and named environment files from both contexts, the unused backend `uv.lock` and web browser/cache output. Example templates remain available. | `7769103` |
| Qualification startup and cleanup | Retry transient startup resets/timeouts within the existing budgets. Diagnostic log retrieval cannot prevent owned resource cleanup or result creation. Real cleanup failures remain failures and preserve original errors; tested image IDs are recorded. | `017f580` |
| Reproducible vendor qualification | Added a local OpenSearch 3.8.0 runner with bounded readiness, the CI resource caps, loopback binding, exact ownership, source/image identities and durable results even when diagnostic retrieval fails. | `04227b6` |
| Recovery gate truthfulness | Recovery teardown rejects failed Compose cleanup and still cleans temporary files. Host-loss evidence records source/project and cleanup state, remains failed after cleanup failure, and preserves the original restore error. | `14546c5` |
| Image/MCP gate cleanup | Dependency-image verification rejects a failed container removal. MCP teardown attempts every owned container/network even after one removal fails, rejects a successful run with failed cleanup, and preserves the original qualification error. | `46871e5` |
| Fresh image vulnerability gate | The newly refreshed scan database identified fixable TIFF `CVE-2026-4775` in the web image. Its build now requires `tiff>=4.7.2-r0`; actual OS inventory, metadata and legal records were regenerated and verified. | `17f8aea` |
| Ambiguous MCP fixture creation | Requested network names enter cleanup scope before Docker creation, because a timeout can hide a successful daemon-side create. Two regressions reproduce the omitted internal/ingress network before correction. | `65f4a75` |

The AI changes have 42 focused tests passing. Thirteen newly added regressions
failed against the previous implementation before the gate/provenance fixes.
Independent review-package regeneration is byte-identical.
Qualification-script regressions pass **17/17**; eleven failed before the fix.
An actual Docker context/export probe confirmed the previous exposure and the
new exclusions using only synthetic files, including nested environment files.
The new vendor runner has **12** passing simulated failure/ownership tests;
recovery cleanup/evidence has **5** passing regressions.
Image/MCP cleanup has **7** passing failure and completion regressions.

## Qualification evidence

The full backend suite passes **4,569 tests**, with two explicit capacity/recovery
harness skips, in **20m 08s** under the unchanged 25-minute timeout. Combined
statement/branch coverage is **87.04%**, reporting coverage is **87.23%**, and
every critical module floor passes. All 93 runtime pins plus development/MCP
declarations were verified in a fresh Python 3.12.3 environment. The backend
tree remained identical from suite launch at `04227b6` through the final source
candidate. The later MCP cleanup change is covered separately; its startup
function remained identical. Both database/Redis fixtures were removed. See
the [backend evidence](evidence/2026-10-08-remediation/backend-coverage.json).
The final TIFF packaging commit preserves those exact backend/helper/API objects.
The last MCP fixture-bookkeeping amendment also preserves the backend/API tree
and the startup function exercised by the backend tests, and has separate
operations coverage. Evidence retains the actual suite revisions rather than
claiming a new full-suite run at those later commits.

Clean-environment operations discovery at `65f4a75` passes **47 tests**, with one live
host-loss opt-in skip. Recovery discovery passes **121 tests**, with four live
Docker opt-in skips. Those skips do not count as live qualification results.

The [live OpenSearch 3.8.0 contract](evidence/2026-10-08-remediation/opensearch.json)
passes under the unchanged CI resource caps. It verifies two distinct external
actions, explicit recovery of a lost acceptance response without an additional
launch, persisted external search identity, withdrawal history and omission of
source documents from results.
The [host-loss drill](evidence/2026-10-08-remediation/host-loss.json) passes using
the backend image built at `46871e5`: original source environment removed, reconstruction
from an independent archive and encryption key, encrypted data restored,
migration 0125 head, outbound quarantine and runtime DDL denial. Both live
qualifiers independently confirm that their owned resources were removed.
The later TIFF commit changes only web packaging; the tested backend source,
runtime pins and recovery tooling remain identical, as the applicability record
verifies. The evidence keeps the actual image ID and run revision.
These are disposable local fault-domain simulations, not actual operator
backup or vendor-permission qualification.

Frontend unit tests pass **164 files / 1,355 tests**. Lint, production build,
distribution smoke, browser TypeScript and the unchanged `npm run audit` gate
pass. Full npm audit reports **zero vulnerabilities at every severity**.
The 2.1.0 manifest and both lockfile version fields agree.
Fresh [browser evidence](evidence/2026-10-08-remediation/frontend.json) records
**138/138 interaction cases** and **63/63 real-server cases** passing across
Chromium, Firefox and WebKit. The real server runs the exact locked Python
environment, including patched PyJWT/urllib3, without an import overlay.
Static/interaction checks launched at `17f8aea`, with identical frontend/app
trees verified against `65f4a75`; real-server checks launched at `65f4a75`.
All browser containers, service fixtures and launcher processes were removed.

Tailwind compatibility was compared against the previous CSS across Chromium,
Firefox and WebKit, 390/1440px viewports, light/dark themes and forced-colors
requests: **24 states × 12 representative elements, zero compared-property
differences**. This is sampled computed-style compatibility, not a claim that
every pixel or older browser is supported. This comparison completed before
the host restart; its temporary raw files were lost, so it remains an explicitly
recorded observation. The fresh full browser matrices provide separate current
evidence. The new native-control regression passes all three engines. The
supported browser floor is disclosed in the release notes.

Fresh native AMD64 images built from a Git archive of `17f8aea` have matching
2.1.0 and revision labels:

| Image | Immutable local image ID |
|---|---|
| Backend | `sha256:d543b34901a1df2e51f5a723369189bb479642ae7acb6c9122aa969bd4c22509` |
| Web | `sha256:43e1a5130e4f8017d61cde5cc22c5d08c170b58360c7ecf4a15956c9b51322c7` |

The later `65f4a75` changes only the host-side MCP qualifier and its operations
tests, outside both Docker build contexts. Application, runtime dependencies,
Dockerfiles and Compose configuration remain identical to the image-build
revision. Those image labels retain the actual build revision.

The [fresh-image smoke](evidence/2026-10-08-remediation/native-smoke.json) passes
bootstrap login/session/logout, bounded uploads, successful and unavailable
upstream log privacy, the published API version/digest, theme initializer,
migration head and exclusion of local environment/lock files. It also verifies
non-root/read-only/capability boundaries and capped memory/CPU/PIDs, API readiness
during export CPU pressure, a separately contained OOM, and Beat/export-worker
restart recovery. Containers, volumes, networks and the pressure probe were
independently confirmed removed.

Final [dependency/legal verification and both startup modes](evidence/2026-10-08-remediation/image-startup-inventory.json)
pass. Both [final image scans](evidence/2026-10-08-remediation/image-scans.json)
have **zero fixable HIGH/CRITICAL findings** at the unchanged threshold.
The workflow's complete `vuln,secret` scanner scope also passes both images,
with zero HIGH/CRITICAL vulnerability findings and zero matching secrets.
Trivy 0.70.0 used the database updated October 8 at 15:33 UTC and freshly
downloaded at 18:23 UTC. That database first found TIFF `CVE-2026-4775` in the
previous web image, and the same database verifies the correction without a
suppression. Actual packaged TIFF is `4.7.2-r0`, matching the fix in
[Alpine's security database](https://secdb.alpinelinux.org/v3.24/main.json).
The [pre-correction scan](evidence/2026-10-08-remediation/image-scans-pre-tiff.json)
and initial candidate evidence remain distinct from the final image identities.

The [final MCP proxy checks](evidence/2026-10-08-remediation/mcp-proxy.json) pass
both supported protocols, scoped search/evidence, origin/CORS checks, OAuth
discovery and cookie/CSRF consent, PKCE exchange, code-replay denial, MCP-only
audience enforcement and token revocation. Official SDK 2.2.0 passes automatic
and legacy negotiation. This helper mounts current app/migration/nginx source
read-only; the separate native smoke qualifies the image-only contents. Its
checked cleanup and independent resource queries pass.

The regenerated OpenAPI version is 2.1.0 and its contract SHA-256 is
`a608ae21656a8d50f83919e01a16751720c1199a7f4cd552465770bd3df16aaa`.
API and browser-policy [regeneration](evidence/2026-10-08-remediation/generated-artifacts.json)
is byte-identical in an owned clean working directory with synthetic settings.
First-party remediation Ruff checks and the **1,004-file** source-size gate pass.

The first sustained comparison ran two sequential 600-second workloads at
`35ac120` and `65f4a75`, with 21 identical harness/configuration files, the same
final pinned Python environment, one application CPU, a 1,024 MiB RSS guard,
512 MiB PostgreSQL and 128 MiB Redis limits, and the unchanged 20% regression
threshold. Its scope is review-remediation code under a common runtime. The
historical baseline pinned older PyJWT/urllib3, and already contained the
unreleased integration feature batch; this is not a full released-2.0-to-2.1
dependency-stack or production-capacity comparison. Shared-host pressure is
recorded before and after each run. The
[baseline](capacity/2026-10-08-remediation-baseline-sustained.json) and
[candidate](capacity/2026-10-08-remediation-candidate-sustained.json) workloads
pass every absolute budget with zero task or sampler errors. The unchanged
[comparator](capacity/2026-10-08-remediation-comparison.json) exits **1**:
successful-export p95 rises from **288.198 ms to 439.536 ms (+52.512%)**.
Aggregate export p95 falls from 459.499 ms to 454.799 ms; AI and governance
p95 also fall. Policy-conflict counts differ (7 versus 4), so the aggregate and
successful-export populations are not interchangeable.

The export implementation, workload and authorization/fence paths are unchanged.
The changed label-archive reference check is not called by this workload's
label-description updates. Host pressure differed substantially: CPU pressure's
10-second average was 24.78% before the baseline and 0.90% before the candidate.
Those observations do not establish the cause of the successful-export flag.
An independent candidate-then-baseline pair uses the same durations, limits,
runtime and comparator to investigate order and timing variation. Both pairs'
results will be retained; the first flag is not suppressed.

## Execution history and limits

An earlier attempt overlapped full-suite, capacity and several Docker workloads
under severe shared-host contention. It did not complete those qualification
gates, and the host restart removed its temporary logs. Those observations are
diagnostic history, not retained passing evidence. The fresh static, browser
and live qualification runs above use owned paths and serialized heavy
workloads, with the original budgets and thresholds preserved. The separately
labeled pre-restart CSS comparison remains the exception.

The final interaction fixture's Vite startup reached the host's inotify watcher limit
before executing a test. Polling inside the disposable browser container
resolved startup without changing host settings, assertion scope or timeouts.
No application failure is inferred from that environment error.

## Human and hosted qualification boundaries

All twelve seed cases still await named human review. The generated package
contains no captured model outputs, claim judgments or semantic approval.
Preparation and provider-contract tests cannot supply that evidence. Review the
[case package](../../backend/evaluations/ai-quality/review-package/README.md),
correct annotations if necessary, approve exact revisions, capture the chosen
provider/model outputs and complete the strict promotion gate. Ordinary ingestion
does not require this optional model-promotion work.

CodeQL, ARM64 source-build smoke and the publication workflow's exact-platform
image scan/smoke/promotion must run on the intended source before public release.
They cannot be inferred from local AMD64 results. The existing quality workflow
runs on pull requests and `dev` pushes, with no manual dispatch; a unique
candidate branch and draft pull request can obtain hosted evidence without a
`main` push or release tag.

The documented product boundaries remain deliberate scope: installation-wide
tenant isolation, MCP writes/SSE/prompts/resources/dynamic client registration,
native push to every MISP/SIEM and external deadline notifications are not part
of this candidate. The review found no unexpected stub behind those implemented
workflows. The older [article-chatbot proposal](2026-09-12-team-and-chatbot-proposal.md)
remains a proposal outside the implemented 2.1.0 scope. Actual deployment
hardware, IdP/TLS, receiver permissions and backup
or key custody require operator qualification.

All validation uses owned disposable fixtures. Existing services, credentials,
the pre-existing `backend/uv.lock` and `backups/` remain untouched.

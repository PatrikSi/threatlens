# Code review and release readiness — 2026-10-08

This is the original review checkpoint. See the
[remediation follow-up](2026-10-08-remediation.md) for the audit correction,
2.1.0 candidate and subsequent qualification results.

**Decision: do not release or merge this batch yet.** The implemented feature
set is substantially complete, but the configured frontend dependency audit
still fails. The next version should be **2.1.0** after the remaining gates and
release preparation are complete. This review leaves `VERSION` at `2.0.1`.

This assessment began at `35ac120` on local `main`, which was 141 commits ahead
of fetched `origin/main` (`4ccd249`). The reviewed integration range is
`origin/main...main`. Local `main` also contains `origin/dev`. Review fixes
were committed incrementally as
`Patrik <patrik@local>`.

## Scope and approach

The coordinating review and three parallel code passes covered backend
authentication, team authorization and handling labels; indicator evidence and
publications; MCP and delegated authorization; AI admission, qualification and
section recovery; worker ownership and retries; webhook/receiver execution;
exports and retention; frontend mutations and access-state lifecycles;
migrations, recovery, packaging and release workflows.

Code inspection was combined with focused before/after regressions, the full
backend and frontend suites, actual PostgreSQL/Redis fixtures, pinned MCP SDK
interoperability, browser workflows, disposable recovery drills, image builds,
dependency audits and a concurrent capacity smoke. No deployed services,
existing backup directories or credentials were used as test fixtures. The
pre-existing untracked `backend/uv.lock` and `backups/` were left untouched.

## Findings corrected during this review

| ID | Priority | Confirmed behavior and correction | Commit / validation |
|---|---|---|---|
| CR01 | P2 | Rapid repeated confirmation could issue two saved hunt-view deletes. A synchronous pending guard now blocks concurrent mutations, and the confirmation dialog displays the pending state. | `49914e0`; regression reproduced two requests before the fix; 12 focused frontend tests passed. |
| CR02 | P2 | A handling label could be archived after its live indicator evidence was deleted even though a reviewed publication still retained that label. Publication-label history now participates in the reference guard. | `6c45cf0`; 19 publication/data-policy tests passed, including source deletion, envelope/audit pruning and final publication removal. |
| CR03 | P2 | Qualification recovery requeued an interrupted local reservation but then rejected its persisted `started` checkpoint, even when no provider call had been admitted. Recovery now requires an unchanged request identity and receipt checks that exclude sent or ambiguous calls. | `6b7c19c`; 26 qualification/recovery tests passed. Ambiguous, consumed and legacy unproven starts remain blocked. |
| CR04 | P2 | A synthesis attempt admitted after a budget deferral retained the old deferral marker. Its later provider failure could appear budget-limited and mislead continuation recovery. Admitted attempts now clear the obsolete marker. | `eb12674`; 3 unit and 31 adjacent continuation/extraction tests passed. |
| CR05 | P3 | Hunt worklists advertised a claim action while the related assessment was queued/running; the mutation then returned 409. Capability projection now considers the task state. | `0eb3108`; 36 worklist/review/MCP tests passed. |
| CR06 | Documentation | MCP troubleshooting omitted the supported ATT&CK lookup tool for service accounts. | `9b6707e`; exact tool-catalog regression passed. |
| CR07 | Dependency gate | Backend audit found 17 advisory records in PyJWT and urllib3. Runtime pins, inventories and bundled legal paths were updated to PyJWT 2.15.1 and urllib3 2.8.0. | `b27854b`; 158 focused authentication/OIDC/MCP/fetch tests passed under the new libraries; the updated complete runtime lock audit passed. |
| CR08 | Dependency gate | Compatible fixes were available for three frontend development dependencies. Updated brace-expansion, source-map-js and undici without changing dependency declarations or suppressing advisories. | `607f799`; 1,355 frontend tests, lint, build and production smoke passed. Remaining audit failure is described below. |
| CR09 | Migration coverage | Migration 0125 lacked populated legacy-webhook coverage, and an older rollback test stopped before the current head. Added preservation/default/routing checks and included 0125 in the rollback path. | `a2bc06e`; 15 focused migration tests passed. No production migration was changed. |
| CR10 | Test correctness | Two indicator evidence tests assumed the final event for an article was an indicator event. The new `article.ai.ready` event intentionally follows it and has a different payload. Assertions now select `intel.indicators.changed` explicitly. | `1f8ecc4`; reproduced 2 failures before correction; 41 indicator/webhook/event tests passed afterward. |
| CR11 | Release artifacts | Five checked-in frontend OS artifact files disagreed with the newly built image's patched crypto/expat packages. Regenerated them from the actual image. | `49430fe`; native image-pair inventory/legal verifier passed. |
| CR12 | Image gate | Native scans found eight fixable HIGH/CRITICAL package-advisory records in backend PCRE2/Perl and four HIGH records in web PCRE2. Both Dockerfiles now explicitly install the available patched packages; backend minimum-version checks reject stale security packages. OS inventories and release documentation were refreshed. | `0790373`; both images rebuilt, both final scans passed, image-pair inventories/legal files matched, and both native web startup/restart modes passed. |
| CR13 | Documentation | The feature overview omitted the implemented team hunts, indicator publications, automation/OpenSearch and MCP surfaces. Added concise descriptions with implementation guides. | `b27014c`; documentation diff check passed. |
| CR14 | Browser gate | Later browser projects encountered extra global feeds created by earlier export tests, so a global `Edit` locator became ambiguous. A shared helper now selects the exact named fixture card; accessibility/auth/export assertions remain unchanged. | `fc7baa1`; full real-server matrix passed 63/63 across Chromium, Firefox and WebKit with accumulated fixture state; TypeScript, scoped ESLint and diff checks passed. |

The OS fixes are PCRE2 `10.42-1+deb12u2` and Perl `5.36.0-7+deb12u4` for Debian,
and PCRE2 `10.49-r0` for Alpine. The Debian fixes are confirmed by the
[PCRE2 tracker](https://security-tracker.debian.org/tracker/CVE-2026-103111) and
[Perl tracker](https://security-tracker.debian.org/tracker/CVE-2026-13221).
The retained [image scan evidence](evidence/2026-10-08-review/image-scans.json)
records the original findings, final image IDs and zero remaining fixable
HIGH/CRITICAL findings at the configured threshold. Trivy 0.70.0 used a database
updated on October 7 and downloaded on October 8; a direct GHCR database request
was denied, so the successful fresh mirror download was used for every scan.

## Remaining release conditions

### R01 — the frontend audit is a confirmed merge gate failure

[`web/.audit-ci.jsonc`](../../web/.audit-ci.jsonc) enables the high-severity
threshold, and the exact `npm run audit` command exits 1. After the compatible
updates, npm reports **5 high and 2 moderate dependency entries**, all reachable
through development tooling. These counts include transitive package entries
inheriting an advisory; they do not represent seven distinct vulnerabilities.
`npm audit --omit=dev` reports **zero** vulnerabilities.

The high finding originates in `braces` through micromatch/fast-glob and
Tailwind 3's chokidar path. The upstream uncontrolled-recursion report remains
open, and the current `braces` line has no patched release. The moderate
selector-parser finding has a fix in 7.1.6 outside the current Tailwind 3
dependency range. The parser advisory concerns untrusted selectors; the review
did not demonstrate an exploitable public ThreatLens request path through this
build tooling. See the [braces upstream report](https://github.com/micromatch/braces/issues/70)
and [selector-parser advisory](https://github.com/postcss/postcss-selector-parser/security/advisories/GHSA-rj75-hqrm-r3gf).

Resolve the dependency chain in a separate, reviewed change, or explicitly
decide a narrowly documented temporary audit exception with applicability,
owner and expiry. The review did not introduce an override, suppress the
advisory, weaken the audit threshold or perform a Tailwind 4 migration.

### R02 — AI semantic promotion is still unqualified

All **12** cases in the [evaluation corpus](../../backend/evaluations/ai-quality/v1.json)
are AI-authored seeds with `pending_analyst_review`, no reviewer and no review
timestamp. Loading this corpus with `require_reviewed=True` correctly rejects
it. The [quality runbook](../pages/ai-quality-and-coverage.md) explicitly separates
provider contract checks from semantic approval; qualification results set
`semantic_quality_approved` to false.

The implemented admission, receipts, schema validation, quoted evidence and
quality gates are present. They do not establish factual accuracy or useful
hunts on representative provider outputs. Complete named analyst review and
exact-revision prediction evaluation, including claim judgments, cost and
latency, before promoting an AI model/prompt under the strict semantic gates.
This is unfinished qualification work, not a requirement to disable ordinary
RSS ingestion or an assertion that every optional AI feature prevents a release.

### R03 — prepare and validate the actual release candidate

Twenty migrations (`0106`–`0125`) and substantial new API/UI capabilities are
pending since `origin/main`. Existing [2.0.1 release notes](../releases/2.0.1.md)
describe only the earlier nginx startup correction. The current version fields
are consistently 2.0.1; they have not yet been bumped for this batch.

After R01 is resolved and the candidate gates are green, use the
[version helper and release process](../reference/release-process.md), regenerate
the OpenAPI artifacts, and write 2.1.0 release notes with the new contract digest,
feature boundaries, migration and coordinated-worker upgrade requirements.
Highlight migration 0123's item identity/index rewrite and downgrade length
guard, 0124's historical-attempt semantics, and 0125's false-by-default article
text inclusion. The README feature overview was updated during this review.

Remote CodeQL, the vendor connector gate, ARM64 source-build smoke, and the
publication workflow's exact-platform-digest scan/smoke/promotion must pass on
the intended commit. Local AMD64 evidence does not substitute for those gates.
The [quality workflow](../../.github/workflows/quality-gates.yml) and
[publishing workflow](../../.github/workflows/publish-images.yml) already encode
these checks. No push, tag, public image promotion or remote merge was made.

## Feature completeness and practical limits

| Area | Current implementation | Qualification or intentional boundary |
|---|---|---|
| Team intelligence | Evidence-backed article extraction and assessments, shared hunt queue, claims, review, deadlines and saved views. | Team membership/permissions and handling-label visibility are enforced; teams do not provide installation-wide tenant isolation. Deadline reminders are in-app. |
| Indicator intelligence | Normalization/provenance, source-revision AI roles, analyst verdicts, suppression and retained review evidence. | Source-reported role/confidence remains distinct from an analyst verdict or independent verification. |
| Reviewed publications | Team STIX/MISP publications, retained evidence/labels, consumers, monotonic revisions and withdrawal history. | Distribution is pull/reimport with consumer reconciliation; native push to every MISP/SIEM vendor is not implemented. |
| Automation and OpenSearch | Signed durable webhook snapshots, same-indicator conditions, receiver credentials, external execution receipts, policy withdrawal/acknowledgement and a bounded OpenSearch connector. | Downstream adapters must acknowledge policy revisions. HTTP delivery acceptance alone does not prove an external hunt executed or a withdrawal was enforced. |
| AI operations | Provider profiles/routing, shared account quotas, section checkpoints, explicit continuation budgets, synthesis limitations and recoverable contract qualification. | Ambiguous provider outcomes block replay; real provider accuracy, price/latency and target-hardware workload still require qualification. |
| MCP | Nine scoped read tools, evidence pagination and registered-client OAuth consent/PKCE/resource binding. | Read-only. No write/tool launch, SSE subscriptions, prompts/resources, dynamic client registration or refresh tokens. |
| Operations/recovery | Split DB roles, bounded workers and scratch, read-only/non-root containers, monitoring, independent evidence, restore quarantine and durable reconciliation. | Operators must qualify their actual deployment topology, IdP/TLS, vendor permissions, backup/key custody and hardware. |

No unexpected implementation stub or further demonstrated P0/P1 application
defect was identified in the inspected paths. This is a scoped review result;
tests and source inspection cannot certify all authorization states, vendor
behavior or model outputs. The main remaining feature work is qualification
and the explicitly documented integration boundaries above.

## Validation evidence

The final full backend run passed **4,530 tests**, with five explicit skips,
in **20m 29s**. Combined statement/branch coverage is **87.03%**, reporting
coverage **87.23%**, and every critical per-module floor passed. The run used
PyJWT 2.15.1 and urllib3 2.8.0 from an isolated import target; the existing local
virtual environment was preserved. Backend source and tests were stable during
this final run. The earlier diagnostic run had 4,517 passes and the two stale
event assertions corrected by CR10.

The full frontend suite after the review fixes passed **164 files / 1,355
tests**; lint, production build and distribution smoke also passed. The final
real-server matrix passed **63/63** across Chromium, Firefox and WebKit,
including the originally ambiguous selectors with accumulated feed state.
That full matrix used the existing backend virtual environment. An additional
**15/15** cookies/OIDC cases passed across all three engines using the changed
PyJWT/urllib3 libraries through a fixture-only import path. The final interaction
matrix passed **135/135**, 45 cases per engine.

Initial browser attempts exposed missing host browser dependencies, a root-owned
output directory, the CR14 selector defect, Docker-network-change errors and one
load-related timeout. Using the pinned Playwright container, fresh disposable
output/cache paths, the corrected selector and sequential test execution cleared
these failures. Existing timeout values and product behavior were preserved.

Pinned official MCP SDK validation passed **119 protocol tests + 2 authenticated
HTTP tests** in an isolated environment. Non-destructive recovery discovery ran
125 tests with four opt-in skips; all **four disposable Docker recovery cases**
were subsequently run and passed. Independent operations discovery ran 24 tests
with one opt-in source-loss skip. The pinned Prometheus alert rules passed.

The full backend's five skips are three optional SDK checks covered by the
separate SDK job and two explicit capacity/recovery harness entry points. The
concurrent smoke was run separately; sustained/large workload qualification was
not performed. Compilation, Ruff, the 1,003-file source-size gate, Bash syntax,
ShellCheck, aggregate whitespace hygiene, runtime lock reproduction, generated
API artifacts and the generated browser policy fixture all passed.

The final [validation summary](evidence/2026-10-08-review/validation.json) records
counts, coverage, audit results, tested dependency versions and unrun gates.
Both rebuilt native images have zero fixable HIGH/CRITICAL scan findings at the
configured threshold; startup/restart and complete dependency/legal artifact
comparison passed. The frontend source audit remains the failing local gate.

The concurrent capacity smoke passed with no budget violations or task/sampler
errors. It exercised simultaneous AI admission, exports, policy mutation and
ingestion recovery. Queue recovery was **8.40s**, AI connection p95 **1.20s**,
export p95 **0.62s**, governance p95 **0.57s**, and observed process RSS growth
**31.3 MiB**. The [retained smoke measurements](capacity/2026-10-08-review-smoke.json)
identify the workload, limits, hardware and tested source revision. These small
synthetic measurements are not a sustained release comparison or a production
capacity promise.

Validation used disposable databases and synthetic providers. No paid provider
calls, actual enterprise IdP qualification, sustained before/after capacity
comparison, host-loss reconstruction or public ARM64 release promotion was
performed in this review.

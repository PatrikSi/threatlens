# Comprehensive review — 2026-11-15

## Scope and checkpoint

Work is in progress on local branch `review/2026-11-15`, starting from
`7426dbe9cc3c9585bd5b217b70521767bc98b17b`. This pass covers the shipped
ThreatLens application across analyst and administrator workflows, accessibility,
API and worker correctness, authorization and secrets, resilience, query and
queue growth, deployment and recovery, dependency security and freshness, and
distribution notices. Existing ADRs and documented release boundaries remain
authoritative; the separately installed SOAR and endpoint profiles do not define
ThreatLens requirements.

Three separate agents review frontend workflows, backend security, and
resilience/scalability. The coordinating agent reviews dependency changes,
reconciles findings, serializes tests and commits, and requests independent
review of the actual final changes. Source inspection is not a browser,
native-vendor, load, or fault-injection test.

The October evidence is historical. Its final quality, browser, and capacity
passes do not qualify new November source or dependency changes. No November
release-readiness claim has been made.

## Review coverage

| Area | Current November status | Evidence or next check |
|---|---|---|
| Analyst/admin UI, forms, state races, keyboard and accessibility | implemented-unverified | Independent frontend source/test review in progress; no new browser run |
| Authentication, authorization, workspace and external-data boundaries | implemented-unverified | Independent backend security review in progress |
| Durable workers, retries, cancellation, leases and recovery | implemented-unverified | Independent resilience review in progress |
| Query, queue, storage and resource growth | implemented-unverified | Source and existing acceptance-test review in progress; no performance campaign |
| Runtime/development packages and container bases | implemented-unverified | Current upstream metadata, advisories, compatibility and notices being checked |
| Migrations, API contract, install/upgrade and dependency artifacts | implemented-unverified | Existing CI commands identified; final-source checks pending |
| Documented feature boundaries and incomplete shipped behavior | implemented-unverified | Trace source, tests and current ADRs before assigning findings |

## Execution constraints and preservation

- Effective author and committer were verified as `Patrik <patrik@local>` using
  `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`.
- Pre-existing untracked `backend/uv.lock` and `backups/` are preserved. Real
  environment files, authentication, remotes and global Git configuration are
  outside this change.
- Initial host observation: approximately 1.5 GiB memory available and swap full.
  Tests will be bounded and serialized. No local browser, active security,
  performance or coordinated release campaign is started by this source review.
- No push, merge, publication, model promotion or deployment is authorized by
  this checkpoint. Public release gates in
  [the release process](../reference/release-process.md) remain applicable.

## Commands and outcomes

| Command/check | Actual outcome |
|---|---|
| `git status --short`, staged/unstaged diff inspection | Only the two preserved untracked paths; no tracked pre-existing edits |
| `git var GIT_AUTHOR_IDENT`, `git var GIT_COMMITTER_IDENT` | Both `Patrik <patrik@local>` |
| Manifests, lockfiles, CI and ADR discovery | Source inspected; dependency and acceptance commands identified |
| `git switch -c review/2026-11-15` | Local review branch created; no remote change |

## Confirmed findings and incremental fixes

### N01 — New password admission (P2)

Registration, administrator account creation/reset and password changes accepted
CR/LF characters that native browser password inputs discard. The shared
`NewPassword` validator now rejects those characters for new credentials only.
Existing login/current-password admission, length bounds, Unicode, spaces and
optional administrator updates retain their previous behavior.

`tests/unit/test_password_admission.py` reproduces 36 failures before the fix,
with 33 compatibility controls passing. After correction all 69 cases pass.
The combined after-run also passes 15 new OIDC policy cases, 29 existing OIDC
cases and eight lock-helper cases (121 total, zero failures/errors). The two
custom embedded-lock fixtures used during that run were subsequently removed:
the actual runtime manifest is flat, and the suspected default lock-generator
defect was retracted after checking file boundaries. No generator change is
included. Ruff passes for the new password and OIDC source/tests.

These focused runs use the existing Python 3.12.3 virtual environment; three
unrelated installed versions differ from the old lock (`markdown-it-py`,
`PyJWT`, `urllib3`). They establish the focused behavior, not a clean runtime
dependency qualification. A separate exact-candidate environment and broader
final checks remain pending.

### N02 — OIDC transport-policy enforcement (P1)

Discovery used the generic RSS-capable redirect transport without reapplying
OIDC's HTTPS and embedded-credential policy to each hop. An HTTPS discovery
response could redirect to public HTTP even when insecure OIDC was disabled.
Previously saved HTTP endpoints also bypassed the current policy on fetch.

`oidc_client.py` now validates the initial URL before creating a network client,
and validates every discovery redirect through the transport's existing
pre-request context. Explicit insecure/private development compatibility is
preserved; token POSTs and authenticated user-info requests still do not follow
redirects. Generic RSS fetching and response cleanup remain unchanged.

The actual redirect loop with HTTPX MockTransport reproduces seven failures and
eight passing controls before correction. All fifteen new cases and all 29
existing OIDC unit cases pass afterward. These tests use deterministic DNS
validation doubles and no sockets; they are not a live IdP or TLS campaign.
The resilience reviewer independently inspected the changed paths, exception
redaction and cleanup and found no additional defect; final committed-byte
confirmation is still required after the remaining fixes.

### N03 — Publisher URL normalization and malformed-link handling (P2)

The article preview sanitizer checked raw script-scheme prefixes before URL
rebasing. Entity-decoded tabs/newlines could pass that check and then normalize
to a script scheme; malformed IPv6 links could also raise during rebasing and
turn otherwise valid publisher HTML into a server error. URL attributes and
`srcset` candidates now use the same URL parser's scheme normalization and omit
values that fail parsing. Safe relative, HTTP, protocol-relative, fragment,
mailto and data-image cases retain their existing behavior.

The new real BeautifulSoup/sanitizer regressions reproduce 19 failures and eight
passing compatibility controls before the fix. All 27 new cases and all twenty
existing preview cases pass afterward, within a 134-case PostgreSQL/integration
and preview run with no failures or errors. Ruff passes. The independent
resilience reviewer found no additional defect in the changed parser boundary.
CSP, iframe sandbox and resource opt-in are unchanged; this is a defense and
robustness correction, not a demonstrated XSS exploit or complete browser audit.

### N04 — Notification recovery progress and reservation fencing (P2)

Maintenance could republish retained integration event/delivery IDs after each
stale lease while their separate notification consumer was stopped. Reservations
now require strictly newer, fresh notification execution evidence for repeat
publication; new unreserved work remains admitted. Sending-delivery recovery
checks both its original worker claim and latest publication reservation.

Independent UI review caught two further edges before this fix was committed:
the adapter initially accepted timestamps from stale/future canary records, and
the existing sending-release comparison allowed an old failure to clear a newer
reservation. New regressions reproduce all four invalid-evidence cases and the
real PostgreSQL old-release race. The adapter now admits only `reason == fresh`,
and sending publication release requires exact reservation ownership. Worker
claim/attempt accounting, authorization, circuits and diagnostic scans are
unchanged.

Before evidence includes eight failing pure wiring cases, five failing real
PostgreSQL progress cases, then the four additional canary failures and one
PostgreSQL reservation failure. The corrected four-file suite passes all 92
cases with no skips/errors; Ruff passes for all eight changed files. The UI
reviewer independently rechecked both corrections and the unchanged surrounding
claim/release definitions. PostgreSQL runs use an owned, isolated PostgreSQL 16
container with 384 MiB memory/no swap, one CPU and loopback-only access; no
production database is used. These are ordinary integration tests, not a real
Redis outage, load or distributed-failure campaign.

Probe admission can defer repair under a sustained backlog. Current-owner
publication failures remain retryable; ambiguous broker acceptance can still
produce duplicates. Neither exactly-once publication nor a fully bounded broker
under every failure mode is claimed. The
[operations guide](../pages/operations.md) describes these limits.

### N05 — Clipboard completion and replacement credentials (P2)

An older pending token copy could dismiss a newly created token, losing its
one-time display. Copy feedback could also mark replacement MFA codes, secrets
or identifiers as copied. The shared clipboard feedback hook now binds status
to the displayed value/context and latest attempt. Token dismissal additionally
compares the exact response in the reducer, including same-update-batch races.
Replacement MFA codes are distinguished even when timestamps match. Current
copy failures retain the manual-storage path; unmounted/stale completions do
not change current feedback or dismiss replacement credentials.

Initial token regressions fail before correction; supplemental real component
tests also expose stale MFA/identifier feedback. The related fourteen-file run
passes all 162 cases, including token reducer, token page, MFA and clipboard
controls. The initial whole-source TypeScript check passes with sampled peak
RSS 732,037,120 bytes. Separate security reviewers inspected the actual clipboard
and tagging bytes and found no additional blocker in this bounded behavior.
Browser clipboard writes already issued cannot be cancelled by these guards;
the guarantee concerns feedback and dismissal, not erasing clipboard contents
or guaranteeing permanent secret storage.

### N06 — Tagging default validation and identity refresh (P2)

The tagging defaults panel submitted blank/zero confidence as an unrelated
fallback value and did not enforce the backend's numeric bounds outside a form.
It also retained write availability after the identity query failed. Saving now
requires finite confidence in `0.05..0.995` and an integer secondary limit in
`0..2`, captures the admitted payload before mutation, and pauses writes when
the current identity cannot be verified. Invalid input and existing drafts are
preserved rather than silently replaced; zero remains a valid secondary limit.

The supplemental before-run exposes twelve tagging failures with three valid
boundary controls passing. All fifteen new tagging cases pass in the related
162-case run; the initial whole-source TypeScript check also passes. Independent
security review confirms the validator matches the backend constraints and
found no additional blocker in this change. This fix does not reinterpret
separate read/write permissions or discard retained user drafts.

### N07 — Denied UI query generations and asynchronous recovery (P2)

Independent review and real QueryClient/DOM controls exposed cached private
responses resurfacing after an authoritative read denial followed by a network
outage, a cached sibling selection, or a late response. The affected workflows
include report libraries/details/editorial content, reviewed publications and
consumers, team integrations, indicators/assessments/suppressions, automations
and webhook credentials, AI activity/children/usage/qualification, alerts,
statistics, OAuth consent and operational diagnostics.

The shared `web/src/hooks/useQueryAccessScope.ts` records a read generation per
QueryClient and narrowly defined permission family. Protected cache entries are
wrapped internally and selected only when they belong to the current allowed
generation. A successful read begun after denial can recover access; an older
response, sibling cache hit or mutation cannot. Mutation cache writes capture
the admitting generation, and parent AI run denial retires dependent child and
source selections. Separate backend permission families remain independent;
feed-catalogue denial must not withdraw permitted statistics. Retained local
user drafts remain distinct from saved server data. The helper masks retained
cache payloads; it does not erase browser memory or replace server checks.

Regression evidence includes 12 failing generation cases with two passing
controls, 16 failing automation/AI/alert cases with 41 passing controls, and 11
failing metadata/diagnostic cases. A peer pass reproduced six further failures
and one compatibility pass: stale assessment regeneration, parent-dependent
AI caches, independent statistics/feed access, late OAuth completion and a
qualification receipt link. Explicit paid qualification remains allowed with
its existing consent, provider-version, idempotency and budget controls while
history is initially loading; history availability is not a new authority gate.

After compatible package updates, the seven-file report/assessment/indicator
suite passes all 73 cases. Other first after-runs found two invalid fixture
selectors and one real statistics error-feedback regression; corrections and
final whole-source tests are in progress. The first full lint run also exposes
complexity/dependency issues that are being corrected without suppressions or
raised thresholds. No final UI qualification is claimed at this checkpoint. The first consumer
inventory covered the existing access-data helper, rather than every raw query
renderer. Independent follow-up confirmed additional raw report-preview and
token-inventory gaps; regression controls and the wider raw-consumer inventory
are now in progress. These surfaces are not marked cleared by the earlier pass.

### N08 — Dependency refresh and runtime-only transport completeness (P2)

The initial installed runtime audit found advisories affecting six packages;
the initial frontend audit reported fourteen vulnerable dependency entries.
Current compatible runtime and development updates have been resolved against
upstream metadata and security notices. The exact backend candidate contains
96 runtime distributions; its fresh audit reports zero vulnerabilities and
zero skipped packages. The frontend candidate resolves the advisory paths,
including `minimatch`, and its install audit reports zero vulnerabilities.
These are current database checks, not guarantees against unknown flaws.

A runtime-only import check caught `packaging` missing from the Celery Redis
transport before development tools could mask it. Declaring `celery[redis]`
properly activates the transport extra and includes `packaging==26.3` in the
lock. All 553 application modules then import successfully in the exact
runtime-only environment with network connections blocked. Development tools
were installed only after that check. The backend Dockerfile also imports the
actual API, worker and Beat entrypoints during its runtime-only build.

Manifests changed: `backend/requirements.txt`, `requirements-lock.txt`,
`requirements-dev.txt`, `requirements-mcp-test.txt`, `web/package.json` and
`web/package-lock.json`. Container bases are updated to Python 3.12.16,
Node 22.23.5 and nginx 1.31.7. The native backend build and its inventories/legal artifacts now pass the
checks recorded below; the web image remains pending. Local backend tests use
Python 3.12.3; native runtime image imports also verify Python 3.12.16.

The frontend lock was refreshed using a private npm 11.21.2 because the bundled
npm 10 resolver failed with its Arborist `edgesOut` error. The complete lock
subsequently installs successfully with npm 10.9.9, preserving compatibility
with the existing CI/container installation path. No global tool configuration
was changed. Major-line upgrades with unresolved compatibility requirements
are deferred; supported maintenance lines are used instead of forcing them.

Independent exact-wheel checks verify upstream digests and full distribution
notices for the changed license-sensitive packages, including Pillow's complete
composite license, the historical PSF license in the feed parser dependency,
and packaging's dual-license files. Fresh image artifacts must retain these
texts before release. This is engineering license triage, not legal clearance.

### Verification corrections and current native backend evidence

The first broad updated-runtime run was interrupted after 12 failures, 651
passes and three skips. Eleven failures correlate with eleven actual OOM kills
in the deliberately small, owned PostgreSQL fixture (384 MiB/no swap, 256 MiB
`tmpfs` data). Raising memory exposed the separate temporary-filesystem limit.
This does not establish a production capacity defect: Compose defaults use a
2 GiB database and persistent storage. The owned fixture was replaced with an
isolated disk volume and a 1 GiB/no-swap limit; no product database was touched.

The remaining stream test used a synthetic ASGI `receive()` that immediately
returned the same completed request forever. Starlette's new disconnect
listener could then starve its file-send task. Both direct file-response test
receivers now wait cancellably after the already-consumed body, matching an
open connection. IAM/data-policy locks, blocked-writer checks, disconnect errors
and artifact cleanup assertions are retained. All 58 cases in the four complete
provider/export test files pass after these fixture corrections. No failed or
interrupted broad run is counted as a full pass. The full backend rerun remains
in progress under the corrected fixture.

The backend source-size check additionally found `integration_events.py` above
its existing limit. It now reuses the identical UTC normalization helper from
`integration_delivery_state.py`, preserving the local alias and behavior.
Source size passes for all 1,009 production files; updated Ruff and compile
checks pass. No size/complexity threshold was raised.

The first native amd64 backend image builds successfully with Python 3.12.16,
the exact runtime lock and actual API/worker/Beat import checks. Its generated
API JSON and Markdown match the checked-in contract byte-for-byte, including
anchor `a608ae21656a8d50f83919e01a16751720c1199a7f4cd552465770bd3df16aaa`.
Trivy 0.76.2 with a fresh database reports zero fixable HIGH/CRITICAL findings,
including the base image's additional bootstrap `pip` package. This filtered
scan does not claim zero findings of every severity or for unpublished flaws.

Actual generated runtime and OS inventories/legal files were copied from a
stopped native image, without starting the app. Independent checks confirm all
96 runtime pins, all 124 legal-file hashes and the full license texts already
verified against upstream wheels. The image's Debian timezone database reports
2026f and contains a valid UTC TZif file. The ANTLR runtime wheel's missing
standalone notice was also confirmed; the exact official 4.13.2 BSD copyright
and conditions are now supplemented under `docs/licenses/` and both compliance
mirrors, with source URL and digest recorded in the backend compliance README.
The rebuilt backend image includes that exact supplement and passes the same
fixable HIGH/CRITICAL scan. The repository verifier confirms every backend
reference inventory/legal file matches that image. Final image-pair verification
and the web build remain pending. These checks do not qualify ARM64, browsers, live
vendors, sustained capacity or release publication.

### HTTP client fixture compatibility

The next broad backend run stops on one failure after 681 passes and three
skips: a legacy-session test was actually sending the earlier opaque cookie.
HTTPX2 orders cookies with different domains differently from HTTPX; the test
had added a domainless cookie alongside the server's `testserver.local` cookie
instead of replacing it. A synthetic header-only reproduction confirms both
orders. Thirty-two session/CSRF fixture assignments across six integration test
files now use the same explicit host domain as server-issued cookies. The
legacy test also asserts the selected cookie is the intended token. Application
authentication and CSRF policy are unchanged.

All 129 cases in the complete MFA/IAM, OIDC, service-account, export credential
lifecycle and lock-order test files pass after this correction; updated Ruff
passes. The enterprise logout control is also rerun explicitly; its result is retained
separately before the next complete backend run. Neither stopped broad run is counted as a full pass.

The updated lock also validates byte-for-byte through the repository resolver.
The isolated SDK environment passes 211 complete recovery/worker/protocol unit
cases and both official MCP 2.3.0 modes against the authenticated ASGI route
(two integration cases, no skips). SDK guards and the operator example now
match the updated optional dependency. Both isolated environments pass `pip check`.

### Remaining ASGI scratch fixture and complete rerun checkpoint

The next broad run reaches 847 passes and three skips with no failures before
being stopped proactively: an independent closed sweep of direct file-response
fixtures found the same immediate-return receiver in the scratch-download unit
module. That module was already loaded in the running process, so editing it
could not correct the pending tests. The partial run is retained and is not a
full-suite pass. The scratch receiver now waits cancellably, preserving legacy
ASGI 2.0 coverage and every descriptor-lifetime, range-response and disconnect
assertion. The other two direct transport fixture modules already use blocking
receivers and explicit ASGI 2.4 scopes. All 18 scratch/transport/policy-lifetime
cases pass afterward, and Ruff passes. This is test-client compatibility; the
production transfer deadline and cleanup implementation are unchanged.

### N09 — cached private reads and stale action admission

Confirmed access-loss regressions now retire cached report previews, report
details and libraries, export contexts, publication metadata, indicator reviews,
team assessments, suppression rules, team selectors and token inventory.
Resource generations belong to the existing session QueryClient. A later
ordinary outage cannot restore a response from before a denied read; only a
new authorized read can establish a current baseline. Mutation results capture
their original admission and cannot grant read access on completion. Ordinary
outages retain useful readable context while actions requiring that context
pause. Existing analyst drafts and independent permission families are retained.

Changed source comprises `web/src/hooks/useQueryAccessScope.ts` and the paired
page controllers, renderers and regression cases staged in the N09 increment.
Independent source reviews cover the frozen implementation; root's current
serial Vitest runs pass all 154 report/export/assessment/team controls and all
70 token/report controls (overlapping tests are not added together). Evidence is
retained in `ui-core-stable-precommit` and `ui-token-stable-precommit` under the
private November review directory. Their log SHA-256 values are respectively
`c214be6a244591c8130a1a2343d2eae2a5186eba42c6e7edfa05bb835c11ab69`
and `735fd5b13ee9cddbada917fc860e56ff860dc75e02275dcec78549c380794644`.
These are synthetic DOM/cache tests, with no browser or deployed-platform claim.

The closed inventory of 72 query consumer files identifies further configuration,
IAM, feed and integration families requiring the same review. Those remain open;
this increment does not establish complete UI or release readiness. Dashboard
controls currently pass 84 cases, while additional extraction and late-result
controls have reproduced separate defects being fixed in subsequent increments.

### Complete backend validation remains open

The next UI increment extends the same scoped-read contract to automation
executions and previews, credential profiles, AI activity and child pagination,
qualification receipts, usage and statistics, MCP consent, operational history,
and alert rules and occurrences. Known team membership withdrawal is captured
synchronously from actual query failures; fresh team metadata alone cannot
authorize old occurrence evidence. Canonical evidence reads must recover too.
Independent final source reviews are recorded for these frozen implementations.
The current serial automation/statistics run passes all 132 cases, with log
SHA-256 `e7810e4c6a22ce67ca616a88a1ffe45817e2f5c3c95cbb5e68a0ccbcff4ff782`.
The latest team recovery run passes 30 cases; its earlier fixture was corrected
to keep canonical evidence unavailable instead of returning a genuinely fresh
authorized response. No permission assertion was removed. These commits retain
the explicit remaining configuration and shared-catalogue review gates above.

A reused test database contaminated a later broad run: six OIDC audit assertions
selected old rows, and two retention assertions had persistent scan state. The
same eight unchanged cases pass on a newly created isolated database. The
private runner now creates a fresh database per check, matching CI's normal
fixture lifecycle. The next fresh-database broad run reached approximately 78%
before its sampled process-group RSS exceeded the review runner's 1 GiB limit;
it also displayed one failure before termination. No full pass or coverage gate
is claimed. The next step is bounded fresh-database shards with complete failure
reporting and combined coverage, followed by the remaining release checks.

### N10 — bounded migration search must also bound allocation

The fresh backend run's failing ordinal identifies migration 0079's existing
20-label bounded-search test. Source review finds exponential allocation before
the 4,096 hash-attempt budget: the migration eagerly extended a list with every
nonempty subset. A new guarded-generator regression fails before the fix without
requiring an out-of-memory event. Lazy `itertools.chain` now preserves the exact
candidate order and quarantine, ambiguity and cache behavior while stopping
candidate generation with the search budget. Changed files are
`backend/alembic/versions/0079_metric_captured_taint.py` and
`backend/tests/unit/test_migration_0079.py`.

All three cases in that complete test file pass on a fresh isolated PostgreSQL
database, including upgrade/downgrade controls; Ruff passes. Before/after logs
are `migration-0079-subset-materialization-before` (one failure) and
`migration-0079-lazy-subsets-after` (three passes, log SHA-256
`eaf4a628e50dce42984391813aeb2d488fa7426df26ad20ca1440c5929f4dc8a`).
Independent review of the exact two-file freeze reports no remaining finding
within this change's scope (proof SHA-256
`9168e96f17c7fe943318e23137ed9a075eab347761cec1ff0ba9febc00b327d7`).
This is a confirmed production migration fix; complete sharded suite and combined
coverage validation remain in progress. The first 943-case shard passes.

### N11 — dashboard evidence, previews and automatic read actions

Dashboard reads now use independent cache namespaces with shared feed, view and
tag authority where appropriate. Denied article details retire an open original
preview; recovery cannot reopen the old intent. Extraction refresh/continuation
also refresh the current Dashboard detail key, and continuation pauses during a
detail outage. Automatic mark-read retains its original admitted source while
the inspector mounts; remembered loss of the exact article prevents a cached
row from restarting the action. Other readable rows remain usable.

The increment changes the Dashboard controllers/renderers, shared extraction
components, the existing scope helper's read-only lookup and seven regression
files (21 files total). All 97 cases in the final 11-file related run pass;
log SHA-256 is `2b4014971b12775f0b58445747ad263597192fc805cb0926c4c4cb13bfed98da`.
The final 14 production files have independent source review with all hashes
matching (proof `9310093d5d1e5490670c3122a15c11c54c3d02d6bd2a021ca46c80046534afc6`).
An additional delete-intent lead passed its before control and was withdrawn;
no speculative fix is included. Final project type, lint, build and broader
suite gates remain outstanding. No browser validation is claimed.

### N12 — operational configuration and completion recovery

Alert operations, lifecycle policy/run views and processing recovery now retire
denied saved context and stale confirmation intents while preserving mounted
analyst drafts. A replay acknowledgment cannot turn a failed detail read into a
healthy cached success. Switching occurrences retires a replay confirmation.
Lifecycle queue acknowledgments keep the analyst's current route after navigation;
accepted work is still reconciled through canonical reads. Processing acceptance
and cancellation likewise reload the authoritative destination/version.

The increment changes ten production files and seven regression files. The
complete eight-file related run passes all 49 cases, including the original
idempotency, cancellation and asynchronous navigation controls (log SHA-256
`585bca3eb75b5466ee6a87edff319d7b2cccbc83501f8e89a98da3c198158ffc`).
Independent review covers the final corrected source and canonical-read fixture
changes (proof `890729ca8d7f8edd5f262e91e8055064ccc7472e1dc0351f1b1dec9dd4a3853e`).
One final test-file formatting correction removes an extra trailing blank line.
No assertion is removed. Project-wide type/lint/build and release gates remain
open; these synthetic checks are not a production fault-injection campaign.

### N13 — feed catalogue, encrypted inventory and saved feed drafts

All seven `/feeds` query producers now use the same scoped cache contract;
Dashboard and statistics retain their independent keys and share feed authority.
Feed mutations reconcile those namespaces through canonical reads. A mutation
response cannot repair a failed read. Recent feed articles have their own
namespace, keeping the remaining legacy raw item-cache writer compatible.
Dirty feed drafts survive hidden saved metadata, require deliberate reload after
withdrawal, and retain navigation protection. Delete/bulk intents retire on loss;
RPC guards check the current canonical feed inventory. Encrypted inventory has
independent read history, while an authorized degraded 503 payload still reports
its actual critical state. Webhook team-choice paging cannot restore an older
cached page after denial; static local choices remain usable.

This increment changes 13 explicitly staged files: the feed controller, detail
dialog and page, their tests, the paired feed producers in AI/notification/SMTP/
tagging/team configuration, statistics and webhook choices. Team destination
editor changes remain in a separate pending increment. The final six-file run
passes all 62 cases, log SHA-256
`16e04375b7d196dcd87ee10f5e7fd701404c81299caa1e621442ff45db56eaf2`.
Before controls reproduce five feed defects, a denied health-count disclosure,
lost hidden-draft navigation protection, and cached team-choice restoration.
The degraded-health and static-choice positive controls pass. Legacy mocked
presentation fixtures now declare the actual cache envelope and query state.

Expanded TypeScript and ESLint runs report no errors in these 13 files. Whole
project checks remain open for other in-progress changes. Independent review of
the exact 13-file freeze is requested and pending; this tested increment is not
a release-readiness claim. The private index freeze records every staged hash,
including the feed-only hunk in team configuration.

### N14 — complete backend shards and coverage

All six fresh-database shards now complete: 4,906 passed, zero failed, four
skipped (4,910 collected cases). The skips are the two explicit capacity/recovery
campaign entry points and two optional SDK tests in the base environment. The
official SDK's two protocol modes were separately exercised successfully in the
isolated SDK environment; those results are not added to the base-suite count.
No active capacity or recovery campaign is claimed.

The final six passing data files combine to 87.11% overall branch coverage and
87.25% reporting coverage. The repository's overall/reporting thresholds and all
58 critical module floors pass through `scripts/check_coverage.py`. Actual runs,
JUnit files, coverage inputs and outputs are retained in the private November
directory, with the aggregate in `backend-v6-full-suite-receipt.json`. The earlier
stopped runs, the failed original fifth shard, and one mistyped collector attempt
remain separate unsuccessful evidence and are not counted as passes.

The original fifth shard's three failures were script-loader fixtures importing
without the `scripts/` path that real CLI execution supplies. A per-test restored
path fixture fixes that collection-order dependency. All 17 qualification-script
unit cases pass independently; the corrected complete fifth shard passes 951.
The remaining MCP proxy SDK guard/help text now matches the pinned optional
2.3.0 SDK. CLI `--help` and Ruff with the repository backend configuration pass.
Independent review of these exact two files reports no finding in scope (proof
`e515cf6668542dc1d1a613d9fbf93fff2fce67290d04de696b7f6fd9406e9c52`).
This does not claim a new live SDK proxy run; that native qualification and a
backend image rebuild containing the migration fix remain pending.

### N15 — tagging saved context and asynchronous writes

Tagging bundle reads and their cache writers now share one scoped contract;
read-only preview requests retain independent authority. Dirty settings/rule
drafts stay mounted behind absent saved-content panels during withdrawal and
require explicit fresh-baseline reload. Open delete/reapply intents and private
preview receipts retire. Actual RPC entry checks current read health, identity
and draft signature; late saved responses cannot repair a failed read or replace
a newer draft. Independent initial write flows retain their existing contract.

The change touches four production files and four changed regression files.
All 12 new before cases fail on the original behavior; the complete five-file
after run passes all 43 cases, including existing accepted-create, older-inventory
cancellation and rebase controls. Log SHA-256:
`5b5c27b104cf8906ae8a0ff3cfcf911d7bc5738de1caa1ac7578c290e590c38c`.
Static fixtures now model canonical envelopes/mutation lifecycle; untouched SSR
defaults are correctly clean, with real typed dirty-navigation controls retained.
Independent final source/test review matches all freeze hashes (proof
`169e360b76d711fa0d0b409025f4c7c23b6375989c126a4b117c5d38510ad9f9`).
Project-wide final type/lint/build validation remains pending.

### N16 — investigation canonical reads and mutation admission

Investigation list/detail and dependent evidence, member, note and activity
reads now retain denial history independently. Dirty drafts survive hidden saved
context. Actual RPC entry checks the current canonical detail, closing the
experimentally confirmed ordinary-503 event-to-render race. A late successful
write cannot replace a failed canonical read with a healthy cache entry; the
committed receipt remains available while a real GET reconciles current state.
Expected-version checks, independent child permissions and draft rebasing remain.

The change covers 12 production files and four regression files. All 58 cases
in the complete four-file run pass, including the passing403 control, formerly
failing503 admission control and late-acknowledgment read-health control. Log
SHA-256: `9d30aebb8eac6c508237106dc338e1ab6920d482b4d82d07f6df384d2356c0b6`.
Independent final review matches the exact final source/test freeze, closing the
earlier unverified leads (proof
`978391d47397c2a80cd4b8655ecfccf7c433a461a46a21965097f73d1bd73fb4`).
The expanded whole-project typecheck snapshot passes; the full lint snapshot
reports no investigation errors. Final project validation remains a release gate.

The pending N13 independent feed review is now complete against commit
`fcfc2e04`, with every one of its 13 source/test hashes matching. Proof:
`e08f7508245d287896d2e90780956ded916be79cbee4233da23ad80d86104811`.
Its alternate-reader confirmation lead passed the actual control and was
withdrawn; no speculative production change was made.

### N17 — AI reprocess target admission

Article candidates now have their own scoped namespace, preserving compatibility
with the legacy raw Dashboard item-cache writer. Selected source/article metadata
is bound to its read generation. Ordinary outages retain context and pause target
submission; denial retires the selection without silently broadening its payload.
A deliberate scope reset can return to an independently authorized lookback job.
Initial lookback queueing still requires no article-picker read. Actual selected
target RPC entry rechecks canonical cache health and selected identities.

Three files change: AI settings, its existing presentation fixture and a real-cache
target regression file. Six negative before controls fail and the independent
initial-lookback control passes. The final three-file run passes all 49 cases,
including explicit reset, source recovery and original delayed-scope completion
controls. Log SHA-256:
`f98ec723092619a98f6f605bf076e4471a4c2dec5e7dc2d5931ecab252ed546f`.
Targeted lint passes without threshold changes. Independent review verifies all
three final hashes (proof
`04f9f29638ed5475da8d1ba146afbdc76f21f562a7fb3858aa320fcfc8564395`).
Final whole-project checks remain pending.

### N18 — quality-gate cleanup and stronger fixture evidence

Operational error/selection presentation and lifecycle target membership now use
small helpers that meet the existing complexity/type rules. No threshold or
suppression changes are used. All 49 related cases pass again after extraction
(log `9cb213124ed38b54164563fb6941dc358f0716372fbffd59a7558e7630f2a42d`),
with independent exact-source review
`ac245e0f94e4780b61dc4b4f359e54cb63beeebf171b4d881cf7959c1420f1f0`.
The tagging controller's single oversized physical line is wrapped only;
independent whitespace-equivalence proof is
`752950fa6448b1d70f96f13b0090c63e3a704c7b9ebb748d975b5b8681687252`.

The feed session-fencing fixture now actually returns the eight batch targets
from its canonical inventory, preserving both original five-request assertions
and the replacement-session failure. The actual batched denial/alternate-reader
confirmation control passes, so its speculative lead is withdrawn. All 15 feed
and saved-draft controls pass together (log
`44cafa7808dc2790507e63bc0bc1bd6b5a9af4dd2b8511b869bf4f5676bee830`).
The expanded TypeScript snapshot passes. Final whole-project gates are pending
the remaining IAM/reporting/delivery fixes and dependency artifact checks.

### N19 — notification, SMTP and team destination read boundaries

Notification/SMTP collection, analytics and per-destination delivery/test history
now use scoped reads and captured writes. The shared personal webhook cache is
migrated atomically with its team-destination reader. Parent withdrawal requires
new child reads; a refreshed parent cannot expose old child configuration or
receiver inventory. Receiver actions check current parent health at actual RPC
entry, and late credentials cannot restore withdrawn access.

Dirty controller state survives absent saved editor bodies. This avoids exposing
unchanged private fields copied alongside an edited name, including nested request
data. Fresh canonical rows restore the local draft while old baselines remain
paused until explicit reload. Ordinary outages retain readable context. One-time
and test diagnostics remain scoped; generic completion notices do not copy private
diagnostic payloads. Static template variables/defaults remain raw by design,
verified as hardcoded backend constants.

All 48 delivery cases in eight files pass after the final correction, including
the 19 original failing negatives, the two dirty-field disclosures, private SMTP
diagnostic and real-cache recovery controls. Log:
`e6672945ed7e6294f3d2d6d02bb10446038fd7351a85488dbea8691012278cef`.
All 14 team destination cases pass, including independently reproduced parent/
child and receiver event-to-RPC failures (log
`e260355e835904f5275a67bcb52cd58fe0001d69f7d64b451b4d383f1a422f59`).
Independent final reviews match the exact nine-file delivery and two-file team
production revisions, with their regression files: proofs
`411235156a61484469c715575b1001bf88a2f010eefecde68a170d3914fcd070`
and `db144fd2678c92e33243470274148188223f0ca7da27559b73f0970c3d67c59a`.
Targeted lint and the 1,016-file source-size gate pass. Final project type/build,
dependency artifacts and platform checks remain open.

### N20 — audit export and identity settings recovery

Audit filtering, saved rows and export receipts share scoped read history. Export
checks the live successful canonical read at RPC entry; a late response after
withdrawal cannot download the old payload. All 12 audit cases pass, including
late exports and cached sibling navigation (log
`26b44b262856f01bd3ac9c565517076320ddb739435c2ebd04d786d534b0ad1a`).

Single sign-on drafts, impact reviews, revision conflicts and test diagnostics
now retain their originating read authority. Save checks the current baseline
and reviewed intent. A confirmed pending-save / failed-read / late-ACK regression
is fixed: the completion preserves the read error and local draft and requests
a fresh canonical read. All 16 identity cases pass (log
`29e93d81690ff35f2fbff113188300e283e878d4cff1aee16377c9d34f1e95b8`).
Independent final source proofs are `2e35f84e606c9b4d04663f08ab7010dc1a4833223f0fb60c1e870e38f8b470eb`
and `2e9c7ef0cdb8f49f35beea2469e968376358aec589f461ced10f240e95bfd0a0`.
Whole-project TypeScript passes; the full lint report has no findings in these
files. Three remaining lint findings belong to the still-open group/workspace/
team configuration units. Full frontend and release gates remain open.

### N21 — dashboard intent extraction and rebuilt backend artifacts

Dashboard saved-view intent state and existing availability predicates now use
small cohesive helpers, preserving all conditions and the existing complexity
limit. All 97 related cases pass after extraction (log
`67bf62db14d4cb5813abeba786f4b772bdd940d38e19a93dfa46755a531739df`),
with independent final two-file proof
`263e681b8925a61f01e1bf3013b6314fa3120732b53d0f0919de305fb353eb02`.
Current project types pass; the global lint report has no dashboard findings.

The tracked backend at `ed571a8b` was rebuilt, including the migration correction,
as native amd64 image `e13c8bbc56c67b9bc098fe0e1a32bffe6e224d19267ff89c48b16b37905f497c`.
Its runtime API/worker/Beat import build checks pass. All 234 extracted dependency/
legal artifacts match the repository exactly. The final-image scan reports zero
fixable HIGH/CRITICAL vulnerabilities (log
`b4e5fad3772263463b3dad6cdabb74b8f95b59fbb36519073931e69ad57f1890`).
An initial scan used the exported config digest in place of the loaded image
manifest digest and failed image lookup; it was not a vulnerability result.
The successful scan explicitly used the owned local daemon and its verified
image manifest digest. This is native image evidence, without ARM or deployment claims.

### N22 — team and AI configuration recovery

Team settings/context/governance, membership choices, hunt worklists/saved views,
provider/routing selection and quota configuration now retain independent scoped
read history. Actual RPCs verify current canonical reads and intent generations.
Dirty drafts retain their original optimistic baseline and explicit reload;
clean drafts adopt a newly verified matching row after access recovery. A missing
saved quota group stays unavailable. Provider deletion intent cannot revive after
a denial and recovery of either its provider or routing read.

All 72 cases in 12 files pass after the final correction (log
`d6b2f3515679a3097e8eec327362be6c3aec573c7d1d01819a9453037413d003`).
This includes eight additional recovery controls and the accepted-save baseline
coherence control. The legacy failed-delete fixture now renders the selected
provider before invoking its delete gesture, preserving its original failure/
selection assertions and adding an exact one-request check. The earlier 71/1
run is retained as fixture evidence. Independent final 15-source review proof:
`7b1b2b590da3eb63ce9d3a079a964dffd039f614dd6b6edb06936e7a6d92cfb3`.
Current whole-project types, lint and source-size checks pass. Final full frontend,
reporting retention and artifact/release checks remain open.

### N23 — directory, access groups and workspace role policies

User-directory reads and group member candidates share authority history, with
independent per-group membership/role reads. Cached sibling pages, selected MFA
reset targets, removal confirmations and copied feedback retire after read loss.
Live RPC checks close event-to-render gaps. Own-account MFA/session operations
retain their independent contracts. Group member rows distinguish loading and
unverified cached state from a genuinely empty result.

Workspace role-policy edits and reset intents retain their read generation;
ordinary personal preferences remain independent. Shared saved-view readers now
use the common scoped envelope. Dirty edits retain their baseline until explicit
reload. Small pure selectors preserve the existing complexity threshold.

All 37 user-directory, 21 access-group and 20 workspace cases pass. Final group
and workspace logs are `45d84b91e6639ea603b0de477d2f528066d6d88847ec784a4414bb5d94cbf7af`
and `ac5b4161e568552bc8b098b40ddfe546048b5279eb755160019e78370fe97119`.
Independent final source proofs: directory `9bd48b9021c86dbf4d36e1f1feaef95ca99e53c687e9ef3c4d76e11d51ff2fd8`,
groups `ddd68f6d90b1667f0732e850ccccb76d0959e002172d87556fdd70405a5d1e70`,
workspace `6b48d37b9f21c8a628c28d4801c0fa1c15697c352c1547f4bae8a5d3a17bee91`.
Whole-project types, lint and source-size checks pass for this snapshot. Full
frontend, remaining reporting retention and native web checks remain open.

### N24 — credential collection partial-write recovery

A reproduced late single-profile save could merge an old collection and clear a
current collection GET error, reopening writes to a cached sibling profile. The
merge now preserves collection read health and requests a canonical refresh.
Actual RPC admission also checks current collection health, selected identity
and baseline generation, closing a separately reproduced queued-handler gap.
Healthy initial profile creation keeps its existing independent contract.

All 18 cases in four related files pass (log
`1977e5b1d529965ec877636f9898b703831309a66ca7758e77ad3c780d7be238`).
Independent final source/runtime-evidence review:
`4c431865ec080b8ac95c6ab0960dfbbf6b7af1e31494d432cc03d799df8d8b6c`.
The token sibling-page probe passes: existing invalidation starts the canonical
read and pauses revoke during re-entry. That speculative defect is withdrawn;
the positive control is retained and token production code is unchanged. Other
full-canonical save replies remain separately qualified rather than prohibited
by a blanket GET-only rule. Current whole-project type/lint/size checks pass;
remaining reporting and final release gates remain open.

### N25 — independent reporting metadata and retained drafts

Reporting capabilities, templates and schedules now retain separate scoped read
histories. Saved editor bodies disappear after withdrawal while dirty controller
state remains recoverable. Actual saves verify the current baseline and read
health; partial late replies cannot heal failed collection reads. Independently
permitted schedule edits remain mounted when capabilities are denied.

Copied template objectives require matching fresh authorization. The local
save-as-template mode/name/visibility now live with the outer report draft, so
withdrawing a private body preserves actual user edits and the unsaved-navigation
guard. Untouched copied defaults retire after read loss. Save completion clears
only its submitted form, preserving later input.

All 58 reporting cases in three files pass after the final correction (log
`7c9d3c1fc7a3e062503c03a75360409f4977e88aa9506adf600e86782c0b320d`).
The actual modal draft regression is the corrected `05703c…` run; `97bd…` was
an incorrect generic form selector and is retained as fixture-only evidence.
Independent final seven-source/runtime-evidence proof:
`8b7c1ce8469bdaa4e83cfc76a3da2a92acbe001984acccffbe283c6783607703`.
The final full frontend/type/audit/build pipeline is running; its outcome is not
substituted by these focused passes.

### N26 — bounded browser withdrawal history and capacity assessment

Idle resource scopes can now be garbage-collected while their session client
remains alive. A conservative per-client summary uses at most 64 KiB; unique
withdrawal/recreation epochs prevent stale copied envelopes from matching a new
scope. Read-only checks preserve first-detail withdrawal history after the detail
query disappears. A live authorized exact scope overrides the summary. No timed
or size eviction discards known withdrawals. See ADR 0008 for the availability
tradeoff and GC-dependent weak-table/live-query memory limits.

The actual Node 22.23.5 `--expose-gc` probe fails its intended collection assertion
with the unchanged strong store and passes after the correction (logs `50c978…`
and `89f0ac…`). Seven deterministic `queryAccessStore.test.ts` controls pass.
The Dashboard collision/recovery DOM control uses the actual production store,
QueryClient and UI, with mocked HTTP and deterministic summary saturation. It
proves preview/automatic actions remain paused until a fresh detail read succeeds,
then verifies the iframe and one actual mocked write. Its combined run with two
AI-save controls passes (log `02ae0d0fc5bd4736273f3f4a96932193a1e0be3065ad7b299375cf187dac313e`).
A subsequent explicit-null detail fixture type annotation preserves runtime data.
Final project types and lint pass; full frontend/build gates remain running.
Independent final source proof is `caa175141581072d34ee932006190561e3e2fc714fb0bdd7ca242f7dd7cd2333`.

The separate capacity assessment records actual configuration/work bounds and
unknown arrival rates, p95, storage and provider limits. Independent numerical/
source review is `0397c0d4c17df274729cdea643b4b290dd72cc5f55d7d7b1a01e9d4d6c591cb7`.
No performance campaign, browser heap/OOM threshold or deployment sizing result
is inferred from source review, synthetic historical budgets or Node collection.

### N27 — complete-suite fixture corrections and precise recovery copy

The first full frontend run collected 1,717 cases in 209 files: 1,714 passed and
three failed. All failures were obsolete raw-cache assertions after scoped cache
adoption. AI save tests now inspect the envelope value while preserving visible
model/budget, actual request and later-edit assertions. The shared editor contract
unwraps both feeds and tagging; an explicit two-row prerequisite prevents its old
tagging lookup from silently treating a valid envelope as absent. All 12 editor
contract cases pass, with independent source/runtime-evidence review.

The second full run collected 1,718 cases in 210 files: 1,717 passed and one export
fixture failed. Its native setter ran before the restored capabilities observer
rendered the filter input. The fixture now waits for that actual input before
using fake timers, preserving the changed-filter request and withheld-preview
assertions. These failed full runs remain failed evidence.

Reporting now distinguishes an access-generation change from a genuinely newer
template revision; reload behavior is unchanged. All 85 related export/reporting
cases pass after both corrections (log
`6efbe6cf1715ea85436780d886bed293c921a73412e1d6fde99918488a6cb3ad`).
Independent final copy/export-readiness proof is
`8f7156c952b235cd4b2ac28f0fa1b197f561fb7379d7f5693998975b3d2bc831`.
The existing operations recovery lead is withdrawn by a passing real-cache
positive control: all four health reads must finish before disclosure/actions
recover. That control is retained. Full types/lint/test/audit/build/bundle checks
are running again on an immutable source snapshot; their result remains pending.

### N28 — compatible frontend dependency refresh

Updated 24 direct dependencies and the minimatch override, including React,
Query, router, Vite/Vitest, jsdom, lint/type tooling and the browser test toolchain.
The web build uses Node 22.23.5 and nginx 1.31.7. TypeScript stays on supported
5.9: the current typescript-eslint peer range excludes TypeScript 7.

The regenerated lock resolves 114 runtime package entries. All installed runtime
packages have complete top-level legal files available to the artifact generator;
Lucide's full composite ISC/Feather MIT notice is retained. Exact upstream archive
integrity and selected newly changed notices were independently checked. This is
engineering license triage, without legal clearance. Native image/OS inventories
and the nginx-specific copyright notice still require actual-image inspection.

An initial npm 10 lock resolution failed inside Arborist. Private npm 11 resolved
the lock successfully, then the bundled npm 10.9.9 clean install with lifecycle
scripts disabled passed. The native build must additionally verify ordinary
`npm ci`. The candidate audit had zero advisories; the final audit/build pipeline
is still running. Independent exact package/lock/Dockerfile pre-native proof:
`e83d87e9f95fd19429f5a753294c7cce03263e2d08b67855824de768901471b5`.
No application version bump, publication or deployment is included.

### N29 — unhandled runner errors and non-vacuous report lifecycle controls

The third full run reports 1,718 passing assertions in JSON but exits 1. This is
a failed gate. Installed Vitest's JSON reporter omits unhandled errors from that
JSON success calculation; the normal CLI still rejects them. A fourth diagnostic
run with default/JSON reporters and a separate error observer captures two actual
unhandled `Previous report failed` rejections from composed lifecycle fixtures.
No error-ignore option or exit-code override is used.

Those fixtures returned malformed generic arrays for report-library pages and
could crash their route before starting the deferred mutation. They now provide
typed contract-shaped responses, prove canonical admission and exactly one pending
HTTP write before changing routes, and require live current-view content with no
route error boundary afterward. Both delete/retry completion and failure cases
exercise their actual original mutation. All six focused cases pass with CLI 0
and an empty unhandled-error list (log
`a35de3c0dcb996a1ee2949a4c366f3d9ad472006fa59ad9a423513e8f2cce4cd`).
The primary reviewer independently reviewed the separate agent's fixture changes.
Current whole-project types and lint pass; the final full pipeline remains open.

### N30 — exact notices for emitted frontend code and nginx

The runtime dependency graph does not include build tools whose code/CSS can
appear in the production bundle. Added exact published Vite 7.3.8, Rollup 4.68.3
and Tailwind CSS 4.3.5 license payloads for traced preload, namespace/interop and
CSS output, plus nginx 1.31.7's specific copyright notice. All four files are
mirrored byte-for-byte across repository/backend/web bundles under the existing
mirror policy. Archive integrity, version, primary-source URL and SHA-256 are
recorded; this is engineering triage, without legal clearance.

Publisher license files include trailing whitespace. A narrow `.gitattributes`
rule preserves only blank-at-EOL in six exact versioned Vite/Rollup notice paths;
other files and whitespace checks remain unchanged. No global configuration or
CI error-ignore setting is changed. Independent final 15-path source/payload/
documentation review:
`d060cfaf8ecd0e224329c632946471389974fe21c97260a33e07f69ea4b30e5c`.
Both native images still require rebuilding and checking these actual packaged
notices, generated inventories and vulnerability scans. The earlier backend
image evidence remains a prior snapshot. The final frontend audit passes with
zero advisories across all severities (log
`9e6d18af871e16d47d45331422d540e9d3c3a1c29f2862987ea9fa8c5a9980ec`).

### N31 — live post-create notification compatibility control

The fifth full frontend run exits 0 with all 1,718 cases passing and no unhandled
errors. Type/lint, audit, production build and bundle smoke also pass. Default
reporter inspection still reveals one handled route crash inside a positive
notification fixture: its newly created webhook history request received a
generic array instead of the required delivery-page object.

The fixture now supplies that actual response shape and proves the newly created
form/history remain live while the initial collection GET stays pending. It
asserts one POST and its authored payload, one successful new-ID history read,
empty-history presentation, retained name/URL and absence of a route error
boundary. A first stronger check used an exact prefix lookup and failed 19/20;
the lookup now correctly matches the generation-qualified key. No product or
admission policy is changed.

All 20 focused cases pass with CLI 0, no unhandled errors and no route-crash markers
(log `cc9c6093fc9d57fcf8c0c5568d4d93a15f373af16b6bac62d2e729049685bbe8`).
Independent final fixture source proof:
`d512f8622855f51167fd8ad2caaa419ce2934d7f0c40ed728a9d443c1ffc2444`.
The final full run on these exact fixture bytes remains running. Native image
builds, packaged inventory/notice checks and isolated HTTP qualification follow.

### N32 — inherited Alpine security updates

Actual native scanning found ten fixable web-image entries: one CRITICAL and nine
HIGH findings across Brotli, curl/libcurl, X11, XSLT, musl and zlib. The refreshed
nginx tag plus the old five-package upgrade list left these inherited packages
unpatched. Backend image `596072…` passes its fixable HIGH/CRITICAL scan.

The web build now upgrades all installed packages from its configured release
repositories, retaining the existing TIFF minimum. The release guide explains
why a short allowlist is insufficient and links the authoritative Alpine package
manager documentation. Independent exact two-source review:
`e11af21cd702144e9ecce9b7baa8559930177f89e5347b6ef2b48a8a8b045283`.

A candidate built from the tracked application plus the recorded uncommitted
Dockerfile patch passes its actual-image scan with zero fixable HIGH/CRITICAL
entries (log `88607820c368095f8a2d07424c1bd755b881d752bc9f9feb283785cc2e370faf`).
Both non-root web startup/index-HTTP/restart cases pass, including the read-only
filesystem with bounded tmpfs; exact-owned resources are removed (log
`cd0b749375d4ac89b53c4fa4740e9fe20ec3758f4788a9012f8b00ac0ca17291`).
That candidate is qualified as a patch test rather than an exact committed-revision
image. The fixed image must now be rebuilt from the committed Dockerfile, then
rescanned and its final OS inventory, notices and API/proxy runtime rechecked.
Application source is unchanged from the passing 1,718-case full suite.

### N33 — workflow dependency and scanner maintenance

Reviewed all 57 external action uses against the 12 official stable families,
updated six stale major families and pinned every external use to its reviewed
immutable commit with a release comment. Three Trivy steps explicitly select
CLI 0.76.2; their action's default 0.70.0 lags security fixes. Registry attestations
explicitly disable v4's optional storage records, preserving existing permissions
and registry-only behavior. Dependabot now includes both `/` and `/docker`, so
actual build Dockerfiles receive updates.

Four semantic static regressions fail before correction and pass afterward:
immutable refs, safe explicit scanner/failure gates, registry-only attestations
and actual Docker-context coverage. All 15 dependency/Compose contract cases and
two existing fake-process capacity-workflow safety tests pass; these tests do not
run a capacity workload or Docker daemon. The new test passes Ruff. Independent
checks verify exact upstream input schemas and the complete normalized YAML AST,
including unchanged permissions, scripts, artifact handoffs and reusable inputs.

Final seven-source proofs are
`6e001d92c263cf243867546c9830e3a05f083bc57808799a0336c7b3fb1cf0ca`
and `54353d1875f9929df6a7694e80e6a48356f9bdcd706ebddf4ec89518ee76c36e`.
Actual 15-case log: `53265fa255c36d4f4312a17caa20d10ec1ef42ceda66f5454c37e29292d828bd`;
two fake-process controls: `19c9f83b47e055964858e6b44515074ca5b3728d23c31da92fc59ba97e9ce307`.
Hosted Actions, OIDC/GHCR publishing and the installed self-hosted runner version
remain unexecuted. Node 24-capable runner support is an existing requirement.
Application/build inputs remain unchanged; the new backend test is excluded from
images by the existing Docker ignore policy.

### N34 — final frontend and native web evidence

The sixth full frontend run passes all 1,718 cases in 210 files, exits 0 and
records no unhandled errors or handled route-crash markers. TypeScript, full
ESLint, the dependency audit across all severities, production build and bundle
DOM smoke also pass. Full-suite log:
`737de02d9d94ccccc27968b959bb40333e9abf9b441b7382e35f0e79a2d37325`.

The fixed native web image built from `be5b3b0011da4cf554d5ddb5a0264273c34500fa`
is `sha256:1401b143189606f25bf968c3ec7bf260149a20ef2642e96b215c583580746a72`.
Its actual scan contains zero fixable HIGH/CRITICAL entries (log
`10056d463de76140f5f4dde34fbc3776faf8a16e47c34e665ed994f631c184e9`).
The six frontend inventory/legal reference roots now match its extracted
197 generated files, including 114 runtime packages and 72 Alpine records.
All 18 supplemental notices and five fonts were checked against their sources;
application bundle bytes match the host build. The sole extra HTML asset is the
official nginx error page. Native bundle DOM smoke passes.

The actual isolated nginx/API/SDK HTTP qualification and both non-root web
filesystem/startup/restart cases pass, with all resources belonging to that
qualification removed. Log:
`ee446faa87fdcfc0567fce3a78fe9c56414cc33ba185f6370f620ea3376f20f3`.
This uses a source-mounted API/nginx fixture on native dependency images;
it is not a full production-topology or browser result. Independent native
inventory/license assessment:
`66952433bdffb52ff41eb10990f6926731ed10a1113d4a4eb9e00b548a9fa8a7`.

An additional installed-Python audit reopens the backend gate: pytest 8.4.2 and
bootstrap pip in both private test environments have confirmed advisories;
the prior native backend also contains affected pip 25.0.1. The 96 application
runtime pins remain clean. The earlier HIGH/CRITICAL image scan did not cover
these lower-severity bootstrap findings. Test/bootstrap updates, full pytest
compatibility tests, broader CI auditing and backend image requalification are
in progress; no overall release clearance is claimed.

### N35 — Python bootstrap and test-tool security

An audit of all installed distributions found seven unique advisory IDs affecting
pip and pytest outside the 96-pin application audit. Raw advisory records repeat
those IDs; they are not fourteen distinct vulnerabilities. Both independent test
manifests now pin pip 26.3 and the minimum patched pytest 9.0.3, retaining MCP
2.3.0 and the existing lightweight SDK stage. The backend Dockerfile upgrades
interpreter-bound pip before installing the unchanged runtime lock.

Only pip and pytest changed in the private 105-package backend and 113-package
SDK environments. Both `pip check` and strict installed-distribution audits
pass with no findings or skips. The user's original virtual environment was
preserved. Source/environment freeze:
`73a80cc3af9b86aea895e2872ea0b8a259be07778dd85e062594a3f4b1dd6d96`.
Versions and wheel integrity were checked against the publishers' exact-version
[pip](https://pypi.org/pypi/pip/26.3/json) and
[pytest](https://pypi.org/pypi/pytest/9.0.3/json) metadata.

CI retains the separate strict complete-runtime-lock audit and adds installed
environment audits for backend and SDK, including bootstrap/test/auditor
dependencies. The auditor is pinned to 2.10.1 and constrained by the runtime
lock. Minimal migration/recovery install paths derive the same pip pin without
importing the entire development graph. Tests precede auditor installation;
workflow permissions and service scope are unchanged. The release guide
describes this distinction and the image scan's severity limit.

The new CI regression fails before correction, then all 16 dependency/Compose
cases pass on pytest 9.0.3 (log
`ecf57d762b443151696ef4727347520d0d5715719bac1e0cd93caddf627ffa61`).
Ruff passes across backend application/tests/scripts. Regenerating the API and
preview artifacts produces identical bytes; the 1,018-file source-size gate
passes (log `1240dc09a268ed060ee41ac72160613e5f5f3dcf9ba516c0eedf62345a1e2147`).
The actual runtime resolver under pip 26.3 reproduces all 96 pins byte-for-byte.

In a separate SDK environment copy, the constrained auditor installation adds
18 packages and changes none of the existing 113; its actual installed audit
passes for all 131 packages, as does the separate runtime audit. Completion proof:
`43e3757507c73c300e32c5a91856009060461d2eccbf2a975a4f2a42d4f35c1b`.
Independent final six-source review and six fake-process controls for the actual
minimal recovery scripts pass, including installer failures, derived pins,
bounded deadlines and unchanged dependency graphs. The migration extraction and
shell syntax also pass. Independent proof:
`c51ec258e1cbe7258827f3a1ee203357ba1dafe4d421fb129931f14f320984d2`.

The new full backend compatibility run uses six serialized fresh-database shards.
Its result and final native backend image/audit/inventory/runtime qualification
remain pending. The MCP job retains its 15-minute overall ceiling: aggregate
per-command deadlines do not guarantee a hosted duration. Actual hosted Actions
execution remains a separate gate.

### N36 — deterministic recovery-fixture timestamps

The patched-runner full suite passes its first three shards, then the fourth
reports 946 passed, two failed and two skipped. Both failures are recovery
fixtures introduced in N04: their logical clock is fixed at this day's noon,
but the delivery's ORM `updated_at` uses the actual database clock. Once the
review crosses noon, the synthetic canary is older than that row and production
correctly refuses the recovery publication. The failed full-shard log is
`d2ff912c10dc97f6148312842ed370389bfc827896703981793a3120a81e0f44`.

A separate fresh-database reproduction preserves both failures and observes the
actual rows: claims at 12:00, updates at 12:15, and canaries around 12:00:31.
Before log: `8d691fde5b4cb213347f2b4227f3119c488cc0b26f7b970e79f23628eaf8f0fc`.
This is a fixture clock mismatch, rather than evidence of a pytest regression.
The original unsuccessful observer import attempt is also retained separately.

Only `backend/tests/unit/test_integration_delivery.py` changes. Both tests now
exercise past (2020) and future (2100) timelines, persist the post-claim update
timestamp on that same timeline, refresh the actual row and assert both clocks
agree. All existing stale-canary, publication-reservation, worker-claim and
current-owner release assertions remain. All four focused cases pass (log
`94a4007fddf2f5f1d6a99e40aaad084000ef0a6076405c28d424cfd9be6680e9`).

The complete corrected fourth shard passes 950 cases with two skips and CLI 0
(log `4195709d12c10d0627d3f226739b036d8a631587bbc3bc1dec4d5db93e0ba6da`).
The remaining shards are running on fresh databases. Final evidence explicitly
combines the unchanged first three shards with the corrected later runs; the
failed fourth shard is retained and excluded from coverage. Application,
migrations, runtime dependencies and the first three shards' test files are
unchanged.

Ruff passes for the full backend application/tests/scripts. Independent
whole-file AST comparison confirms only the parametrization and explicit clock
setup changed; every previous assertion remains. Both source snapshots differ
only in this test module. Independent proof:
`bc17870941389e7a707255adb516f3d633a018b06bae0f81d8e4964967fd2b9b`.

### N37 — remove unused runtime package installers

The rebuilt backend `f8aac180…` initially fails its raw HIGH/CRITICAL scan for
CVE-2025-47273, attributed to setuptools 70.3.0 in pip 26.3's vendored SBOM.
Two independent reviews of the actual exported image confirm that only
`pkg_resources` is vendored; the affected `setuptools.package_index` module is
absent, including from the dormant ensurepip wheel. This matches the
[maintainer advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf)
and [pip's vendoring policy](https://github.com/pypa/pip/blob/26.3/src/pip/_vendor/README.rst).
The original scan failure is retained as an overbroad component-metadata match,
not rewritten as a successful scanner run.

The runtime does not use pip or ensurepip. The backend build now installs the
unchanged 96 application pins with patched pip, then uninstalls pip and removes
ensurepip before the existing runtime import/inventory checks. Explicit guards
require both modules and all three pip entry points to be absent. Host developer
and SDK environments retain patched pip 26.3. Resolving/installing packages and
automatic venv bootstrapping are host/build workflows; the release guide records
that runtime boundary. No advisory ignore, VEX
exception or modified vendor SBOM is introduced.

Independent review traces startup, migration, workers, health and recovery paths;
the optional unused Kombu etcd diagnostic already falls back when its obsolete
pip API is unavailable. No application source or runtime pin changes. Exact
two-source and actual old-image applicability proof:
`d77cec82671b7de092274b0feeba4efd9c272ba0c8dc11036f0a71027a5a564c`.

The candidate built from tracked application source plus the recorded Dockerfile
patch passes its actual installer-absence and API/Celery/Beat import guards
(log `d2eff1db00fcd120ae23669b5a528a610c6c243d5e8ac718f79267bb0e2afe28`).
All 16 CI/Compose contract cases also pass again. The final committed image,
actual 96-package inventory/legal payloads, all-severity fixable scans and
embedded-backend HTTP qualification remain pending.

One private old-image extraction verifier exceeded its sampled memory limit by
hashing an exported archive in one read. The failed run is retained; recovery
streams the hash, independently verifies all 20,779 filesystem paths and actual
pip payloads, and removes the exact-owned stopped extraction container. A
database-cleanup absence check also required a typed Docker lookup; both owned
database containers and their volumes are now independently verified absent.
These verification failures are not application failures or counted passes.

## Next executable steps

Build and qualify the committed runtime-only image pair, refresh actual package
inventories/notices and complete the embedded-backend HTTP check.
Reconcile the final revision with independent reviews and actual gate receipts,
then remove only owned disposable resources. Browser and ARM CI,
current capacity campaigns and full production-topology qualification remain
unexecuted; no version bump, push, merge or deployment is included.

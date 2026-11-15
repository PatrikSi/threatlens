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

## Next executable steps

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

Finish independent source reviews and the current registry/advisory inventory,
record confirmed findings with reproductions, implement disjoint fixes with
regression checks, and commit coherent tested increments. Reconcile final-source
independent reviews and report any unavailable validation as a remaining gate.

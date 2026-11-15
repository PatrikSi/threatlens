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

## Next executable steps

Finish independent source reviews and the current registry/advisory inventory,
record confirmed findings with reproductions, implement disjoint fixes with
regression checks, and commit coherent tested increments. Reconcile final-source
independent reviews and report any unavailable validation as a remaining gate.

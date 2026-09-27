# ThreatLens security review — 2026-09-27

Reviewed revision: `5e847a8`. This supplements the [comprehensive review](docs/reviews/2026-09-27-post-fix-review.md); SEC01 maps to NR09 and SEC02 to NR05. This was a read-only source and disposable-test review. No application code, live services, credentials, or live data were changed.

No new confirmed authentication bypass, cross-team read, arbitrary code execution, SQL/command injection, or default-policy SSRF defect was demonstrated in the reviewed paths. That is a scoped review result, not proof that the entire product is vulnerability-free. One concrete scratch-ownership defect was confirmed during tests, and the UI reviewer independently reproduced an access-denial presentation defect.

## Confirmed findings

### SEC01 — P2, scratch ownership / reliability: a second database can delete another running export's files

- Location: `backend/app/services/export_job_scratch.py:14` and `:22–34`.
- Creation uses one common temporary-directory prefix, `threatlens-export-job-<job UUID>-<claim UUID>`. Cleanup scans all such paths and removes a directory when the *current database* has no corresponding job, even if another installation/database owns a valid running claim.
- Reproduction: `/tmp/threatlens-security-scratch-probe.py` calls the real creation/cleanup helpers inside an owned temporary root. A mocked database view containing the running job preserves its artifact; a different database view that does not contain the job deletes that artifact while the first job remains running. Retained results: [scratch isolation evidence](docs/reviews/evidence/2026-09-27-post-fix/scratch-isolation-probe.json).
- Observed regression: the combined security suite's machine-export test returned `failed` after `FileNotFoundError`; an isolated rerun passed. The code and bounded reproduction establish the defect. Another concurrent database's cleanup is consistent with the observed failure, but the historical process that removed this specific directory was not instrumented.
- Impact: parallel tests/development instances, or installations using different databases under the same OS identity and shared temporary root, can interrupt each other's exports. Durable database artifacts are not shown to be lost. Container-private tmpfs in the supplied Compose deployment (`docker-compose.yml:539–540`) isolates export workers across containers, so this is not demonstrated cross-container production data loss.
- Fix: give scratch storage an installation/database namespace and have cleanup operate only inside that namespace; isolate each test suite's temporary root. Add a regression proving another namespace cannot be removed, alongside existing same-installation crash cleanup.

### SEC02 — P2, UI access state: Operations retains prior metrics after explicit access denial

- Location: `web/src/pages/OperationsPage.tsx:59`, `:117–122`, `:192–203`.
- The page renders raw cached overview/worker/history/run data after a failed refetch. A 403 is treated as an ordinary last-known snapshot; the actual denial explanation is hidden by that warning.
- UI reviewer reproduced the actual React Query transition in Chromium: initial successful response, subsequent 403, prior database-health text remains visible and explicit denial text is absent. Evidence: `/tmp/tl-ui-review/edgecases.log` and the [retained browser screenshot](docs/reviews/evidence/2026-09-27-post-fix/operations-after-403.png).
- Security scope: this retains information already delivered to the same browser session. Backend operations routes consistently require `read:operations` and return `Cache-Control: no-store`. No fresh unauthorized response or cross-account disclosure was shown.
- Fix: use the existing `accessibleQueryData` helper for all four datasets, distinguish 401/403/404 from transient failures, show the denial explicitly, and disable protected actions until access recovers. Preserve last-known data for transient 503/network outages.
- Count this with the UI review finding, not as a second independent vulnerability.

## Controls reviewed and reconfirmed

- Browser sessions: opaque server-side sessions, strong password hashing, CSRF for cookie-authenticated mutations, scope-separated API credentials, recent-authentication/MFA flows and credential revocation.
- OIDC: signed ID-token validation with supported asymmetric algorithms, issuer/audience/nonce checks, matching UserInfo subject, protected security claims, verified-email provisioning, and no automatic link to an existing account by email.
- MCP OAuth: pre-registered exact redirect URIs, HTTPS/explicit loopback callback policy, resource binding, S256 PKCE, one-time expiring authorization codes, browser-only explicit consent, step-up checks, short-lived MCP-only tokens, current account/client/scope checks, and captured handling caps.
- MCP transport/read paths: explicit `read:mcp` opt-in, feature permissions, team membership checks, caller/credential-bound cursors, exact evidence revisions, bounded input/output/JSON complexity, bounded transfers retaining authorization fences, rate limits and redacted audit metadata.
- Team integrations: membership plus feature permissions and current credential fences; adoption preserves old execution evidence authority; machine receivers are destination-scoped to callbacks/opaque policy updates, with expiry/revocation checks after waits; ordinary API reads reject receiver credentials.
- Publication distribution: consumer credentials convey opaque subscription changes and acknowledgements, not authority to download retained evidence; actual publication reads use team and current/captured handling access.
- Exports: accepting credential scope/current authority, canonical policy→owner→credential lock ordering, handling lineage, bounded transfer lifetime, server-created filenames/private scratch files, and CSV formula neutralization.
- Outbound requests: validated HTTP(S) targets, global-unicast default classification including shared-address rejection, vetted-IP pinning, redirect checks, separate private-network opt-ins, total deadlines and decoded byte caps. AI credentials are bound to the selected destination; public plaintext provider destinations are blocked. Webhook credentialed cross-origin redirects are rejected.
- Untrusted rendering: React escaping, raw-HTML-disabled Markdown with safe URL transformation, allowlist-only reconstructed RSS markup, original-preview CSP sandbox with script/connect/frame/form blocking, external resources blocked by default and opt-in retained as an explicit preference.
- Error/logging boundaries: generic unexpected-error responses, request references, validation output omitting raw rejected inputs, log credential redaction, hidden SQL parameters, and URL query removal from access logs.

## Executed verification

1. `/tmp/threatlens-security-review-tests.log`: **206 passed**, 73.47 s. Unit/integration slice covering security primitives, URL/SSRF and pinned connections, private-network transport, webhook transport, original previews, OIDC, OAuth schemas, MCP consent/policy/lifecycle/credential boundaries, team adapters, team receiver ownership, publication-consumer authorization and webhook test authority.
2. `/tmp/threatlens-security-review-auth-regressions.log`: **179 passed, 1 failed**, 102.06 s. Expanded IAM hardening/MFA/session flows, OIDC handlers, service-account auth, export credential lifecycle/transfer policy lifetime, notification data policy, MCP evidence/read boundaries, and auth throttling. The export failure is described in SEC01.
3. `/tmp/threatlens-security-export-reprobe.log`: the previously failed machine-export test **passed in isolation**, 15.70 s.
4. Bounded scratch-namespace reproduction: passed all assertions, proving the ownership issue without live services or shared temporary files.

## Remaining qualification / architectural opportunities

These are not additional demonstrated vulnerabilities:

- The supplied private-network fetch flag intentionally permits both feed and article requests to internal hosts. For deployments combining internal sources and untrusted public feeds, per-feed/destination allowlists or network egress policy would narrow that opt-in; it is currently documented as a global boundary choice, not a bypass of the default policy.
- Token/SSO/security tests use controlled identities and provider fixtures. Verify actual enterprise IdP claim mapping, proxy trust, TLS/cookie behavior, and ingress headers in the intended deployment. This pass did not penetrate-test a remote host or certify every provider/client implementation.
- UI permission transitions need generated 200→403→recovery tests in addition to existing outage tests, particularly for pages deliberately retaining cached data.
- The concurrent scratch failure means parallel-suite reliability requires explicit filesystem isolation even though PostgreSQL/Redis fixtures are disposable.

Dependencies, CI and full deployment qualification are covered by the parent review, not duplicated here. No paid AI requests or production SIEM launches were made.

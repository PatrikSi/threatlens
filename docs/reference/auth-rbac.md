# Auth, RBAC, and Token Scopes

## Published Paths

- Public/browser-facing API paths are versioned under `/api/v1/*`
- The backend service exposes the same routers internally under `/v1/*`
- Any compatibility aliases outside `/v1/*` are not part of the published contract or OpenAPI schema and are not exposed through the bundled web proxy

## Credential Resolution Order

`backend/app/api/deps.py` resolves credentials like this:

1. If an `Authorization: Bearer ...` header is present, ThreatLens treats it as a scoped personal API token
2. If no bearer header is present, ThreatLens reads the browser auth cookie (`AUTH_COOKIE_NAME`)
3. If neither header nor cookie resolves to an active, approved user, the request fails with `401`

Important contract details:

- A bearer header suppresses cookie fallback. If a request sends both and the header is invalid, the cookie is ignored.
- Browser login does not return a replayable session JWT in JSON.
- Approved and active account status is enforced after credential validation for both browser sessions and API tokens.

## Browser Session Contract

- `POST /api/v1/auth/login` accepts email/password, returns a JSON body (`token_type=session_cookie`, `csrf_token`), and sets:
  - an HttpOnly session cookie (`AUTH_COOKIE_NAME`)
  - a JS-readable CSRF cookie (`AUTH_CSRF_COOKIE_NAME`)
- The shipped React frontend uses the cookie session and does not persist session credentials in browser storage.
- CSRF checks apply only when authentication comes from the session cookie and the method is `POST`, `PUT`, `PATCH`, or `DELETE`.
- Cookie-authenticated mutating requests must send `AUTH_CSRF_HEADER_NAME` with the same value as the CSRF cookie.
- `POST /api/v1/auth/logout` also enforces CSRF when a session cookie is present.
- `GET /api/v1/auth/registration-settings` is anonymous and exposes whether self-registration is enabled.
- `POST /api/v1/auth/register` is anonymous when `ALLOW_SELF_REGISTRATION=true`, creates a user record, and leaves the new user pending approval.
- `GET /api/v1/auth/me` includes an additive `authentication` object describing the credential kind and, for opaque browser sessions, the actual `session_auth_method`, MFA method, recent-auth validity/expiry, external-MFA boolean, and relative reauthentication endpoint. `recently_authenticated` is the canonical validity field; `recent_authentication_valid` remains an equivalent compatibility alias. The external-MFA boolean is recomputed against the current configured ACR/AMR policy; raw OIDC claims are never exposed. Hybrid accounts must use this session value rather than `provisioning_source` when choosing a step-up flow.
- `DELETE /api/v1/auth/security/sessions/{session_id}` revokes only that exact session. Revoking the current session clears its cookies; revoking a non-current session leaves the current cookie, sibling sessions, and account auth generation unchanged. `POST /api/v1/auth/security/sessions/revoke-others` remains the explicit account-wide sibling-session action. Both destructive paths require recent authentication. The browser restores the requested exact-session or revoke-others action after local or OIDC verification, but requires the user to review and confirm it again; reauthentication never performs the revocation automatically.
- `POST /api/v1/auth/security/reauthenticate` verifies a local password and, when local MFA is enabled, a current TOTP code. Recovery codes are not accepted. It rotates only the initiating opaque session and returns `session_id`, `authenticated_at`, and `valid_until`.

## JWT Behavior

Defined in `backend/app/core/security.py`:

- Algorithm: `JWT_ALGORITHM` (default `HS256`)
- Claims used:
  - `sub` (user UUID)
  - `exp` (expiry)
  - `ver` (user auth-token version)
- Expiry: `JWT_EXPIRES_MINUTES` (default 1440)
- Password changes increment the user's auth-token version, which invalidates previously issued JWTs and cookie-backed sessions.
- Admin updates that change `email`, `password`, `role`, `is_active`, or `is_approved` rotate `auth_token_version`, revoke active browser sessions and API tokens, and cancel pending MFA enrollment so an older identity or privilege state cannot remain authenticated.
- Email, role, active-state, and approval updates should send the loaded `expected_security_version`. A stale supplied value returns `user_security_version_conflict` with HTTP 409 and the current version in `X-Current-Security-Version`. Legacy requests may omit the precondition for backward compatibility; the mutation is serialized under the same database locks and invariants, and unversioned security or password changes are recorded in logs and audit history.
- A change that would remove the final active, approved administrator returns the stable `last_active_admin` conflict and is written to the audit log as a rejected operation.

Legacy browser JWTs remain accepted during the compatibility window, but newly
created browser sessions use random opaque credentials stored only as hashes in
PostgreSQL. A per-user authentication generation invalidates both formats after
email, password, role, approval, account-state, MFA, or administrator reset changes.
Session activity updates are deliberately best effort: a transient bookkeeping
write failure does not reject an otherwise valid request.

## Local Multi-Factor Authentication

- Local-password accounts can enroll a TOTP authenticator from **Account > Account Security** after confirming their password.
- Enrollment secrets expire if they are not confirmed, and cancelling setup deletes the pending credential server-side.
- Successful enrollment returns single-use recovery codes once and rotates the current browser credential while revoking every copied or legacy session credential.
- A TOTP code or unused recovery code can complete local sign-in. Reusing a TOTP time step or a consumed recovery code is rejected.
- Replacing the full recovery-code set requires the local password and a current six-digit TOTP code. A recovery code cannot authorize replacement of every recovery code.
- Disabling MFA requires the local password plus a valid second factor and revokes other browser sessions. Administrator reset additionally revokes the account's API tokens and requires a recorded reason.
- MFA enrollment and privileged MFA actions use shared account and client-IP throttles. Privileged factor checks fail closed with a retriable `503` response when Redis is unavailable, so a multi-replica deployment cannot bypass a distributed lockout. Password sign-in retains its bounded in-process emergency limiter for availability.
- OIDC-only accounts continue to use their identity provider's MFA and recovery controls. ThreatLens records provider MFA assurance only when the signed ID token contains an exact `mfa` authentication-method reference.

Migration `0060_iam_hardening` is an application compatibility boundary. Stop
API and worker processes, apply the migration, and deploy the matching release
before accepting traffic; older processes cannot create or validate the opaque
session and MFA records. Downgrade is blocked while any active local TOTP
credential or delegated API token exists so operators cannot silently remove an
enabled factor or temporarily disable descendant-token revocation. Delegation edges
recorded by supported older releases are restored from their transactional audit
records during upgrade. Direct downgrade is blocked whenever an OIDC provider is
configured because dropping its monotonic revision would make stale provider writes
possible after re-upgrade. Disable or inventory MFA credentials, revoke delegated
tokens, and use a verified database backup/restore procedure for an OIDC-configured
deployment. Downgrade takes bounded exclusive IAM locks and fails with recovery
guidance when application processes or database transactions are still active.

## OpenID Connect

ThreatLens supports one enabled OpenID Connect provider per deployment. Configure it from **Settings > Identity** as an admin.

Provider registration:

1. Enter the provider's exact issuer URL, client ID, client authentication method, and requested scopes.
2. Enter the public ThreatLens origin. Register the callback URL shown by the UI exactly at the provider.
3. Save, then run **Test connection** to verify discovery and the provider's signing-key set.
4. Enable the provider after the redirect URI and client credentials are registered.

The bundled proxy callback is `https://<threatlens-host>/api/v1/auth/oidc/callback`. Direct API deployments can set `OIDC_CALLBACK_PATH=/v1/auth/oidc/callback`. Redirect URI comparison at the provider should remain exact.

For Authentik's default per-provider issuer mode, use the issuer shown in the provider's OpenID Configuration. It normally ends in `/application/o/<application-slug>/`; the Authentik root origin alone is not the provider issuer. Register ThreatLens's displayed callback as a strict redirect URI. The issuer hostname must resolve from the ThreatLens `api` container, which can be checked with `docker compose exec api getent hosts <authentik-host>`.

HTTPS is the secure default for both the IdP and callback origin. Local development can set `ALLOW_INSECURE_HTTP_OIDC=true`; private or internal IdPs additionally require `ALLOW_PRIVATE_NETWORK_OIDC=true`. Existing deployments that already enabled private-network OIDC retain private-HTTP compatibility, but setting both flags is recommended to make the plaintext and private-network trust decisions explicit. Never use plaintext OIDC across an untrusted network.

Administrator MFA recovery is fail closed and follows the current session's actual authentication method, including for hybrid local-plus-SSO accounts. A locally authenticated administrator must verify the current password and their own local MFA. An OIDC-authenticated administrator must first use the CSRF-protected `POST /api/v1/auth/oidc/reauth` flow, which requests `prompt=login` and `max_age=0`, remains bound to the initiating opaque session and account generation, and rotates that session after validating signed `auth_time`, `acr`, and `amr` claims. Plain OIDC login is not treated as MFA. `AUTH_OIDC_ADMIN_MFA_AMR_VALUES` defaults to `mfa`; an empty AMR allow-list disables OIDC authorization for these sensitive actions. `AUTH_OIDC_ADMIN_MFA_ACR_VALUES` can additionally constrain accepted assurance classes, for example `urn:company:loa:2`.

OIDC provider create, update, enable, and disable operations accept either an active, approved administrator API token with `write:users` (including a matching wildcard scope) or a recent opaque administrator browser session. API-token requests remain subject to administrator-role, token-scope, optimistic-revision, break-glass invariant, and audit enforcement; browser reauthentication does not apply to API-token clients. Browser sessions can refresh recent local proof through `POST /api/v1/auth/security/reauthenticate`; OIDC sessions use `POST /api/v1/auth/oidc/reauth`. Local MFA-enabled administrators must prove a current TOTP. OIDC reauthentication always forces `prompt=login` and `max_age=0`, and the resulting signed claims must match the configured ACR/AMR MFA policy or the operation returns `oidc_mfa_assurance_required`.

Disabling the enabled provider is rejected with `oidc_break_glass_admin_required` unless at least one active, approved administrator has local password sign-in available. Test that account before an IdP maintenance window. If role synchronization would demote the final active administrator, ThreatLens keeps the administrator role, permits sign-in, and records a failed `oidc.role.sync` audit entry with reason `last_active_admin` so the mapping can be repaired without locking out the deployment.

Provider writes use optimistic concurrency. Existing configurations require the loaded `expected_config_revision`; an explicit revision of `0` means "create only if no provider is configured." Omitting the field remains accepted for older clients. A stale update or concurrent create returns `oidc_provider_revision_conflict` with the current revision and does not overwrite the newer configuration.

Recent-auth errors use stable codes. `local_reauthentication_required` and `oidc_reauthentication_required` include `error.context.action`, `reauthentication_method`, and a relative `reauthentication_endpoint` suitable for the API client. `browser_session_required`, `opaque_session_required`, and `session_inactive` distinguish token auth, legacy sessions, and rotated/revoked opaque sessions. A successful local step-up returns JSON; a successful OIDC step-up redirects with `oidc_reauth=success` and rotates only the initiating session.

Creating a durable API token from an OIDC-authenticated browser session uses the same configured MFA assurance check. Recent SSO without matching external MFA returns `oidc_mfa_assurance_required`; API-token delegation remains governed separately by scope and child-token lifetime limits.

Recovery-code hashes include a non-secret HMAC key identifier. The encrypted-data inventory reports active, previous, legacy-unversioned, and missing-key dependencies. Do not retire a previous `APP_DATA_ENCRYPTION_KEY` while `key_retirement_blocked` is true; regenerate affected users' recovery codes first. A missing referenced key is critical because those unused codes can no longer be verified.

Protocol and identity behavior:

- Authorization Code flow always uses PKCE S256, a signed short-lived transaction cookie, `state`, and `nonce`.
- Discovery issuer matching is exact. ID tokens require a supported asymmetric signature and validated issuer, audience, expiry, nonce, and access-token hash when present.
- Discovery, token, JWKS, and UserInfo requests use DNS-pinned outbound connections, bounded timeouts, and bounded response bodies. Unexpected endpoint redirects are rejected; discovery redirects are revalidated and capped.
- The durable identity key is `(issuer, subject)`. ThreatLens never uses email as the external identity key and never automatically links an existing local account by email.
- Linking an existing account starts through a CSRF-protected request from an active browser session, requires the current local password and current TOTP code when local MFA is enabled, and binds a fresh provider authorization flow to that exact initiating session. Unlinking requires the current local password and is blocked for OIDC-only accounts.
- New authorization and linking flows request recent provider authentication and require a validated `auth_time` claim. Linking additionally requests an interactive provider login; stale, missing, or future authentication times are rejected with a restartable error.
- Provider access tokens and ID tokens are not persisted. The client secret is encrypted with `APP_DATA_ENCRYPTION_KEY` and is never returned by the API.
- Ordinary OIDC sign-in accepts providers that omit `auth_time`. Linking an identity to an existing local account always requests fresh provider authentication and fails when the signed ID token does not prove a sufficiently recent `auth_time`.
- `GET /api/v1/auth/oidc/settings` returns a typed `flow_contract`. It names the relative callback/start and post-callback paths, `oidc_error`, `oidc_link`, and `oidc_reauth` query parameters, the `success` result, and separate inventories for JSON start errors and callback redirect errors. In particular, a signed in-progress flow redirects with `provider_configuration_changed` when its provider revision is stale and `callback_rate_limited` when its source-IP callback budget is exhausted. Reauthentication start failures use `oidc_provider_unavailable` or `oidc_reauthentication_start_failed`; invalid unsigned state always uses the login error channel and is not written to durable audit history.

Provisioning and role mapping:

- JIT provisioning is opt-in. New users require a syntactically valid email, and `email_verified=true` is required by default. An administrator can relax the verification requirement per provider for a trusted internal IdP; this also permits well-formed identifiers on internal or reserved domains such as `.local`. Missing, malformed, and duplicate local email addresses are still rejected, and existing accounts are never linked automatically by email.
- Automatic approval is a separate opt-in. Otherwise, the new account is created pending the normal admin approval workflow.
- `role_claim` accepts a claim name or dotted object path such as `realm_access.roles`. Claim values may be a string or a list of strings.
- Role mappings use exact, case-sensitive claim values. When multiple mappings match, `admin` takes precedence over `analyst`, then `viewer`. With no match, the configured default role applies.
- Optional role synchronization runs at each OIDC login. A role change rotates browser sessions and revokes active API tokens. A mapping can never demote the final active, approved admin.
- Fixed-role synchronization records the role it replaced. Disabling the provider or unlinking a local account restores tracked local roles transactionally and revokes affected credentials. Upgraded identities whose pre-upgrade role origin cannot be proven are marked `legacy`; provider disable retains that role but revokes every linked credential, while unlink asks an administrator to confirm the intended local role first.
- Custom OIDC access policies add exact, case-sensitive claim mappings to custom roles and local groups without replacing locally assigned access. Materialized grants are leased for `OIDC_ACCESS_GRANT_TTL_SECONDS`; expired grants are ignored immediately. An expiring OIDC grant may authorize editor work but cannot be the durable basis for investigation ownership.
- Once identities are linked, the provider issuer and client ID cannot be changed. Unlink identities first or retain the existing provider identity key.

Before upgrading an installation that manually created `source='oidc'` rows in the IAM assignment tables, run the preflight against the existing database:

```bash
docker compose run --rm api python scripts/prepare_oidc_access_upgrade.py
```

Migration `0065_oidc_claim_mappings` fails before changing the schema when those unowned legacy rows exist. Review the inventory count, take a verified backup, then preserve the same access as locally managed assignments with `--convert-to-local --yes`. The conversion runs in one transaction, removes only duplicate origins, and refuses to run after managed OIDC claim mappings are installed.

Account ownership and administration:

- Every account has a durable provisioning source: `local` or `oidc`. Linking a local account to OIDC does not change its local provisioning source.
- The user directory identifies local, SSO-provisioned, and local-plus-SSO accounts and lists their available sign-in methods.
- User-directory `q` searches email, role, approved/pending and active/inactive labels, local/SSO/hybrid account labels, and linked provider name. Structured `role` and `provisioning_source` filters remain available.
- Directory responses expose `security_version`. The bundled UI sends it as `expected_security_version` for role, active, and approval mutations; a stale value returns coded `409 user_security_version_conflict` plus `X-Current-Security-Version`. Legacy PATCH clients remain compatible when they omit the precondition, with unversioned security changes explicitly logged and audited.
- Passwords and email identifiers for SSO-provisioned accounts remain owned by the identity provider. ThreatLens does not expose local password reset, email edit, or identity unlink actions for these accounts.
- A linked local account retains its local password and may unlink OIDC after password confirmation.
- When role synchronization is enabled, linked users' roles are read-only in ThreatLens because the next OIDC login would otherwise overwrite a local edit. Active and approved status remain locally managed so administrators can suspend or approve access independently of the provider.

Local password login remains available as a break-glass path for local accounts. Keep at least one separate active, approved local admin and test that credential before enabling SSO. JIT-created OIDC accounts remain provider-managed and cannot be converted into local accounts by assigning a password.

### Rotating the application data-encryption key

1. Back up the database and the current key material before changing it.
2. Set a new `APP_DATA_ENCRYPTION_KEY` and retain the old value in `APP_DATA_ENCRYPTION_PREVIOUS_KEYS` on every API and worker replica.
3. Leave the fallback configured while encrypted records are rewritten. Local TOTP secrets are re-encrypted lazily after a successful authenticator-code verification. A recovery-code hash is moved to the active key after that individual code is used; unused recovery codes cannot be re-hashed without their plaintext and should be regenerated by each local-MFA user before key retirement.
4. Rotate or re-save other encrypted integration and OIDC credentials through their documented maintenance paths, then verify sign-in, connector tests, and a recovery-code regeneration in a staging restore.
5. In an isolated staging restore, remove the fallback key and run the administrator encrypted-data inventory scan plus connector and OIDC tests. Remove the old fallback in production only after that scan is fully readable, every local-MFA user has verified a TOTP code and regenerated recovery codes, and a verified backup contains the new key configuration.

Removing a previous key early can make stored credentials permanently unreadable. Keep fallback keys with encrypted backups for as long as those backups must remain restorable.

Authentik 2025.10 and newer returns `email_verified=false` by default. The preferred fix is a custom `email` scope mapping backed by a real verification attribute. For isolated deployments that already trust Authentik's user enrollment and email assignment, disable **Require verified email** in ThreatLens after reviewing the resulting JIT provisioning risk. See Authentik's [email scope verification guidance](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/#email-scope-verification).

Disabling **Require verified email** permits unverified, well-formed internal email identifiers such as `user@company.local`. Authentik must still return a non-empty email-shaped claim, so ensure the user's Email field is populated and the provider includes an email scope mapping.

## API Token Behavior

Token format and handling:

- Marker constant: `API_TOKEN_MARKER = "tlp"`
- Stored: SHA-256 token hash, token prefix, scopes, expiry, revocation state
- Last usage timestamp (`last_used_at`) is updated on successful auth
- Creation endpoint: `POST /api/v1/tokens` only creates tokens for the currently authenticated user
- Local cookie sessions creating durable API tokens must supply `current_password` and, when enabled, a current MFA code. OIDC cookie sessions instead require recent provider authentication and do not accept or require a local password; the browser preserves the token draft across the explicit OIDC reauthentication redirect and still requires the user to submit it afterward.
- Admins can list another user's tokens with `GET /api/v1/tokens?user_id=<uuid>` and can revoke any user's token
- Omitting the `scopes` field applies `DEFAULT_API_TOKEN_SCOPES`; sending an explicit empty list is rejected
- Tokens created while already authenticated with an API token can only delegate a subset of the parent token's scopes
- Delegated tokens persist `parent_token_id`. Revoking any token recursively revokes all active descendants, including legacy deeper lineages. The existing `204` response now includes `X-ThreatLens-Revoked-Token-Count`, `X-ThreatLens-Revoked-Descendant-Count`, and `X-ThreatLens-Root-Token-Revoked` so clients can describe the impact without changing the legacy response body.

Revocation/expiry checks:

- `revoked_at` must be `null`
- `expires_at` must be absent or in the future
- Password changes and admin auth-state changes revoke all active API tokens for that user

## Role Model

`backend/app/core/rbac.py`:

- `admin`
- `analyst`
- `viewer`

Helper dependencies:

- `get_operator_user`: `admin` or `analyst`
- `get_admin_user`: `admin`

The fixed role is now a compatibility and break-glass projection inside the
larger access-governance model. Code-owned permissions can also come from sealed
or custom IAM roles assigned directly or through groups. Personal API-token
scopes attenuate that effective permission set; temporary elevation is excluded
from durable-policy decisions and handling-label grants. See [Access Governance
and Data Policy](./access-governance.md) for custom roles, groups, action
approvals, route attestation, and activation behavior.

## Scope Model

`backend/app/core/token_scopes.py`

Resource scopes:

- `read:feeds`, `write:feeds`
- `read:items`, `write:items`
- `read:tags`, `write:tags`
- `read:views`, `write:views`
- `read:alerts`, `write:alerts`
- `read:tokens`, `write:tokens`
- `read:notifications`, `write:notifications`
- `read:ai`, `write:ai`
- `read:users`, `write:users`
- `read:audit`
- `read:stats`
- `read:integrations`, `write:integrations`
- `read:reports`, `write:reports`
- `read:health`
- `read:operations`, `write:operations`
- `read:investigations`, `write:investigations`
- `read:iam`, `write:iam`
- `read:workspace`, `write:workspace_preferences`, `write:workspace`
- `read:service_accounts`, `write:service_accounts`
- `read:elevations`, `write:elevations`, `approve:elevations`
- `read:approvals`, `write:approvals`, `approve:approvals`
- `read:access_reviews`, `write:access_reviews`
- `read:data_policies`, `write:data_policies`

Wildcard/admin scopes:

- `read:*`
- `write:*`
- `admin:*`
- `*:*`

Default API token scopes:

- `read:feeds`
- `read:items`
- `read:stats`
- `read:alerts`

Evaluation rules:

- Exact scope match grants access.
- `action:*` wildcard grants access for that action.
- `admin:*` and `*:*` grant all.
- `write:<resource>` implies `read:<resource>`.
- If token scope list is empty and `ALLOW_LEGACY_UNSCOPED_TOKENS=true`, scope checks are bypassed for token auth.

## Endpoint Auth Summary

Paths below are relative to the published `/api/v1` base.

| Endpoint group | Role requirement | Scope requirement |
|---|---|---|
| `/auth/me`, `/auth/change-password` | authenticated user | none |
| `/feeds` write ops | `admin` or `analyst` | `write:feeds` |
| `/feeds` read/export/metadata | authenticated user | `read:feeds` |
| `/items` read ops | authenticated user | `read:items` |
| `/exports`, `/exports/preview`, `/exports/capabilities` | authenticated user | `read:items` |
| `/reports`, `/reports/{id}`, report downloads, templates read | authenticated user | `read:reports` |
| report preview/generate and private templates | `admin` or `analyst`; owner-only retry/delete unless admin | `write:reports` |
| report schedules and shared-template mutation | `admin` | `write:reports` |
| `/items` mutate triage (`read/star/note/tags`) | `admin` or `analyst` | `write:items` |
| `/alerts` read | authenticated user | `read:alerts` |
| `/alerts` mutate | authenticated user | `write:alerts` |
| `/alerts/preview` | authenticated user | `read:alerts` and `read:items` |
| `/alerts/matches` | authenticated user | `read:alerts` and `read:items` |
| `/notifications/template-variables`, `/notifications/analytics`, `/notifications/webhooks`, `/notifications/webhooks/{id}/deliveries` | authenticated user | `read:notifications` |
| `/notifications/webhooks` mutate/test/retry | `admin` or `analyst` | `write:notifications` |
| `/integrations/*` | effective permission | `read:integrations` / `write:integrations` |
| `/operations/*` | effective permission | `read:operations` |
| `/ai/*` | `admin` | `read:ai` / `write:ai` |
| `/tags` read | authenticated user | `read:tags` |
| `/tags` create | `admin` or `analyst` | `write:tags` |
| `/tagging/*` | `admin` | `read:tags` / `write:tags` |
| `/views` read | authenticated user | `read:views` |
| `/views` mutate | authenticated user | `write:views` |
| `/tokens` | authenticated user | `read:tokens` / `write:tokens` |
| `/users` | `admin` | `read:users` / `write:users` |
| `/auth/oidc/provider*` | `admin` | `read:users` / `write:users` |
| `/auth/oidc/login`, `/auth/oidc/callback`, `/auth/oidc/settings` | anonymous flow | none |
| `/auth/oidc/account`, `/auth/oidc/link` | authenticated user | none |
| `/audit-logs` | `admin` | `read:audit` |
| `/audit-logs/export` | `admin` | `read:audit` |
| `/stats/*` | authenticated user | `read:stats` |
| `/iam/permissions`, `/iam/roles`, `/iam/groups`, `/iam/effective*` | effective permission | `read:iam` / durable `write:iam` |
| `/iam/service-accounts` | effective permission | `read:service_accounts` / durable `write:service_accounts` |
| `/iam/elevations` | effective permission | `read:elevations` / `write:elevations` / `approve:elevations` |
| `/iam/action-approvals` | effective permission | `read:approvals`; lifecycle-specific durable requester/approver authority for mutations |
| `/iam/access-reviews` | effective permission | `read:access_reviews` / durable `write:access_reviews` |
| `/iam/data-policies` | effective permission | `read:data_policies` / durable `write:data_policies` |
| `/workspace/*` | effective permission | `read:workspace`, `write:workspace_preferences`, or durable `write:workspace` |
| `/health`, `/health/ready`, `/health/live` basic status | none | none |
| `/health/worker`, `/health/beat`, `/health/notifications`, `/health/encrypted-data` | `admin` | `read:health` |

## Practical Trust Notes

- Cookie sessions are the primary browser contract. Token scopes only apply when the caller is authenticated via a personal API token.
- `write:<resource>` implies `read:<resource>` during scope checks.
- `ALLOW_LEGACY_UNSCOPED_TOKENS=true` weakens token authorization by allowing empty-scope legacy tokens to bypass scope checks. Production settings reject this mode.
- Notification webhook targets are validated on create, update, test, retry, and delivery. Public targets must use `https`; private-network or internal-only targets require `ALLOW_PRIVATE_NETWORK_WEBHOOKS=true`.
- Viewer-role access and API tokens without `write:notifications` receive webhook configuration with secret-bearing values redacted. Operator cookie sessions and write-scoped operator tokens retain the existing editable response.
- User updates are serialized around the active-admin invariant; concurrent demotions cannot remove the final active, approved admin.
- Non-admin token revocation is owner-constrained and returns the same not-found response for foreign and nonexistent token IDs.
- OIDC role synchronization and admin user edits share the same serialized final-admin invariant.
- Navigation policy never grants access. `/settings/access` and its optional
  inventories are permission-gated in the frontend, while every underlying API
  independently evaluates current IAM, credential attenuation, handling policy,
  and any object relationship.
- Persistent IAM, data-policy, action-approval, and access-review mutations use
  durable authority and sensitive-browser checks where declared. A scope on a
  personal API token does not bypass a browser-only endpoint.

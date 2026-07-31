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

## JWT Behavior

Defined in `backend/app/core/security.py`:

- Algorithm: `JWT_ALGORITHM` (default `HS256`)
- Claims used:
  - `sub` (user UUID)
  - `exp` (expiry)
  - `ver` (user auth-token version)
- Expiry: `JWT_EXPIRES_MINUTES` (default 1440)
- Password changes increment the user's auth-token version, which invalidates previously issued JWTs and cookie-backed sessions.
- Admin updates that change `password`, `is_active`, or `is_approved` also rotate `auth_token_version` and invalidate existing JWTs/sessions.
- Role changes rotate browser sessions and revoke active API tokens so old privileges cannot survive a promotion or demotion.
- Email-only changes do not rotate credentials.

## OpenID Connect

ThreatLens supports one enabled OpenID Connect provider per deployment. Configure it from **Settings > Identity** as an admin.

Provider registration:

1. Enter the provider's exact issuer URL, client ID, client authentication method, and requested scopes.
2. Enter the public ThreatLens origin. Register the callback URL shown by the UI exactly at the provider.
3. Save, then run **Test connection** to verify discovery and the provider's signing-key set.
4. Enable the provider after the redirect URI and client credentials are registered.

The bundled proxy callback is `https://<threatlens-host>/api/v1/auth/oidc/callback`. Direct API deployments can set `OIDC_CALLBACK_PATH=/v1/auth/oidc/callback`. Redirect URI comparison at the provider should remain exact.

Protocol and identity behavior:

- Authorization Code flow always uses PKCE S256, a signed short-lived transaction cookie, `state`, and `nonce`.
- Discovery issuer matching is exact. ID tokens require a supported asymmetric signature and validated issuer, audience, expiry, nonce, and access-token hash when present.
- Discovery, token, JWKS, and UserInfo requests use DNS-pinned outbound connections, bounded timeouts, and bounded response bodies. Unexpected endpoint redirects are rejected; discovery redirects are revalidated and capped.
- The durable identity key is `(issuer, subject)`. ThreatLens never uses email as the external identity key and never automatically links an existing local account by email.
- Linking an existing account requires an active browser session and a fresh provider authorization flow. Unlinking requires the current local password and is blocked for OIDC-only accounts.
- Provider access tokens and ID tokens are not persisted. The client secret is encrypted with `APP_DATA_ENCRYPTION_KEY` and is never returned by the API.

Provisioning and role mapping:

- JIT provisioning is opt-in. New users require a syntactically valid email with `email_verified=true`.
- Automatic approval is a separate opt-in. Otherwise, the new account is created pending the normal admin approval workflow.
- `role_claim` accepts a claim name or dotted object path such as `realm_access.roles`. Claim values may be a string or a list of strings.
- Role mappings use exact, case-sensitive claim values. When multiple mappings match, `admin` takes precedence over `analyst`, then `viewer`. With no match, the configured default role applies.
- Optional role synchronization runs at each OIDC login. A role change rotates browser sessions and revokes active API tokens. A mapping can never demote the final active, approved admin.
- Once identities are linked, the provider issuer and client ID cannot be changed. Unlink identities first or retain the existing provider identity key.

Local password login remains available as a break-glass path. Keep at least one active, approved local admin and test that credential before enabling SSO. JIT-created OIDC accounts do not have local password login until an admin sets a password from **Users**.

## API Token Behavior

Token format and handling:

- Marker constant: `API_TOKEN_MARKER = "tlp"`
- Stored: SHA-256 token hash, token prefix, scopes, expiry, revocation state
- Last usage timestamp (`last_used_at`) is updated on successful auth
- Creation endpoint: `POST /api/v1/tokens` only creates tokens for the currently authenticated user
- Cookie-session callers creating durable API tokens must also supply `current_password` in the request body as a step-up confirmation
- Admins can list another user's tokens with `GET /api/v1/tokens?user_id=<uuid>` and can revoke any user's token
- Omitting the `scopes` field applies `DEFAULT_API_TOKEN_SCOPES`; sending an explicit empty list is rejected
- Tokens created while already authenticated with an API token can only delegate a subset of the parent token's scopes

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
- `read:health`

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
| `/items` mutate triage (`read/star/note/tags`) | `admin` or `analyst` | `write:items` |
| `/alerts` read | authenticated user | `read:alerts` |
| `/alerts` mutate | authenticated user | `write:alerts` |
| `/alerts/preview` | authenticated user | `read:alerts` and `read:items` |
| `/alerts/matches` | authenticated user | `read:alerts` and `read:items` |
| `/notifications/template-variables`, `/notifications/analytics`, `/notifications/webhooks`, `/notifications/webhooks/{id}/deliveries` | authenticated user | `read:notifications` |
| `/notifications/webhooks` mutate/test/retry | `admin` or `analyst` | `write:notifications` |
| `/integrations/*` | `admin` | `read:integrations` / `write:integrations` |
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

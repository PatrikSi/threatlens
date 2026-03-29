# Auth, RBAC, and Token Scopes

## Authentication Modes

`backend/app/api/deps.py` resolves users in this order:

1. Bearer JWT (`/auth/login` issued token)
2. Cookie JWT (`AUTH_COOKIE_NAME`)
3. Personal API token (`tlp_<public_id>_<secret>` format)

If neither resolves, the request fails with `401`.

## JWT Behavior

Defined in `backend/app/core/security.py`:

- Algorithm: `JWT_ALGORITHM` (default `HS256`)
- Claims used:
  - `sub` (user UUID)
  - `exp` (expiry)
  - `ver` (user auth-token version)
- Expiry: `JWT_EXPIRES_MINUTES` (default 1440)
- Password changes increment the user's auth-token version, which invalidates previously issued JWTs and cookie-backed sessions.
- Browser flow sets:
  - HttpOnly auth cookie (`AUTH_COOKIE_NAME`)
  - CSRF cookie (`AUTH_CSRF_COOKIE_NAME`)
  - Mutating cookie-auth requests must provide CSRF header (`AUTH_CSRF_HEADER_NAME`)

## API Token Behavior

Token format and handling:

- Marker constant: `API_TOKEN_MARKER = "tlp"`
- Stored: SHA-256 token hash, token prefix, scopes, expiry, revocation state
- Last usage timestamp (`last_used_at`) is updated on successful auth

Revocation/expiry checks:

- `revoked_at` must be `null`
- `expires_at` must be absent or in the future

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
| `/notifications/*` | authenticated user | `read:notifications` / `write:notifications` |
| `/ai/*` | `admin` | `read:ai` / `write:ai` |
| `/tags` read | authenticated user | `read:tags` |
| `/tags` create | `admin` or `analyst` | `write:tags` |
| `/tagging/*` | `admin` | `read:tags` / `write:tags` |
| `/views` read | authenticated user | `read:views` |
| `/views` mutate | authenticated user | `write:views` |
| `/tokens` | authenticated user | `read:tokens` / `write:tokens` |
| `/users` | `admin` | `read:users` / `write:users` |
| `/audit-logs` | `admin` | `read:audit` |
| `/audit-logs/export` | `admin` | `read:audit` |
| `/stats/*` | authenticated user | `read:stats` |
| `/health` | none | none |

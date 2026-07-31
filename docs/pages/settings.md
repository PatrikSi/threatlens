# Settings Section

## Purpose

Centralized account, token, and admin operations.

## Navigation Items

Always visible:

- Account
- API Tokens
- Integrations
  - Webhooks

Admin-only:

- AI (`/settings/ai`) when enabled
- Integrations
  - SMTP
- Tagging
- Identity
- Users
- Audit Logs

Legacy route behavior:

- `/ai` redirects to `/settings/ai`
- `/settings` redirects to `/settings/account`
- `/settings/notifications` redirects to `/settings/integrations/webhooks`
- `/settings/integrations` redirects to `/settings/integrations/webhooks`

## Account Page

- User profile summary (`email`, `role`, `status`, `created`)
- Change password form
- OIDC identity status with link and password-confirmed unlink controls when a provider is enabled
- API calls:
  - `GET /auth/me`
  - `POST /auth/change-password`
  - `GET /auth/oidc/account`
  - `POST /auth/oidc/link`
  - `DELETE /auth/oidc/account`

## Identity Provider (Admin)

- One configurable OIDC provider with Authorization Code plus PKCE sign-in
- Discovery, JWKS connection test, client authentication, scopes, and exact callback URL
- Optional verified-email JIT provisioning and automatic approval
- Configurable default role and exact claim-to-role mappings, including dotted claim paths
- Optional role synchronization on each sign-in
- Client secrets are write-only in the UI and encrypted at rest
- API calls:
  - `GET /auth/oidc/provider`
  - `PUT /auth/oidc/provider`
  - `POST /auth/oidc/provider/test`
  - `GET /auth/oidc/settings`

## Integrations: Webhooks

- Personal outbound webhook notifications for:
  - `rss_item_new`
  - `alert_match`
  - `feed_failing`
  - `webhook_failed`
  - `daily_digest` (backward-compatible API identifier for the AI Daily Brief)
- The AI Daily Brief event is only offered when AI is enabled, configured, and daily briefing is enabled.
- AI Daily Brief delivery uses the persisted system-wide brief; a hook's RSS feed scope does not rebuild or filter the generated brief.
- Saved webhook list with create/edit/delete
- Create, update, test, retry, and delete actions are available to `admin` and `analyst` users with write notification access.
- Viewers can still see their own notification analytics and delivery history when scopes permit.
- Viewer-role reads and read-only API tokens receive redacted URL, header, query, and body configuration values.
- Webhook targets are validated before create/update/test/retry and again before delivery. Public targets must use `https`; private-network or internal-only targets require `ALLOW_PRIVATE_NETWORK_WEBHOOKS=true`.
- Cross-origin redirects are blocked during delivery, and redirect depth is capped by `OUTBOUND_MAX_REDIRECTS`.
- Webhook configuration fields:
  - name
  - enabled flag
  - HTTP method
  - webhook URL
  - query parameters
  - headers
  - content type
  - body mode (`json`, `form`, `raw`, `none`)
  - timeout
  - any feed or selected feeds
- URL query strings entered directly in the URL field are automatically moved into query parameter fields
- Template variables use `{{ item.title }}` style placeholders
- Delivery tooling:
  - test send against a selected sample feed
  - rendered request/response preview
  - recent delivery history per webhook
  - retry button for a stored delivery snapshot
  - analytics cards for success rate, recent failures, event mix, and most-failing webhook
- API calls:
  - `GET /notifications/template-variables`
  - `GET /notifications/analytics`
  - `GET /notifications/webhooks`
  - `POST /notifications/webhooks`
  - `PATCH /notifications/webhooks/{id}`
  - `DELETE /notifications/webhooks/{id}`
  - `POST /notifications/webhooks/test`
  - `GET /notifications/webhooks/{id}/deliveries`
  - `POST /notifications/webhooks/{id}/deliveries/{delivery_id}/retry`

## Integrations: SMTP (Admin)

- Multiple configurable outbound SMTP hooks with per-hook delivery statistics and history.
- Supports enabling/disabling, host, port, security mode, credentials, sender identity, recipient emails, timeout, event types, feed scope, subject template, and HTML template.
- New hooks can store their own authentication or reuse credentials from an existing SMTP hook.
- Changing the `Send for` event loads an event-specific default subject and body template.
- `AI Daily Brief` sends the persisted generated brief immediately after it is ready. Its stable API event identifier remains `daily_digest` for compatibility with existing hooks and delivery history.
- The AI Daily Brief choice, including its membership in `All notification events`, is omitted unless AI daily briefing is available. Historical hooks retain their stored selection without being silently rewritten.
- Historical daily-brief backfills do not send notification emails. Normal scheduled or manual generation for the current brief emits one idempotent event per brief.
- Test tooling can run a connection/authentication check or send a rendered test email to a chosen recipient.
- Per-hook history separates event deliveries from SMTP tests. Test history retains result, action, recipient, saved-versus-draft settings, duration, normalized error details, SMTP server response, timestamps, and run ID.
- Tests against a saved hook are retained even when they use unsaved draft values. A brand-new unsaved hook has no persistent integration ID, so its result is available only in the immediate test response until the hook is saved.
- Test runs do not affect delivery analytics, retries, circuit breaking, or dead-letter counts.
- SMTP test audit entries include the retained run ID when available, duration, normalized error details, and a bounded server response without storing SMTP credentials.
- Dead-letter deliveries can be replayed without rewriting their historical attempt records.
- API calls:
  - `GET /integrations/connectors`
  - `GET /integrations`
  - `GET /integrations/smtp/hooks`
  - `POST /integrations/smtp/hooks`
  - `PATCH /integrations/smtp/hooks/{hook_id}`
  - `DELETE /integrations/smtp/hooks/{hook_id}`
  - `GET /integrations/smtp/template-defaults`
  - `GET /integrations/smtp/analytics`
  - `GET /integrations/smtp/hooks/{hook_id}/deliveries`
  - `GET /integrations/smtp/hooks/{hook_id}/test-runs`
  - `POST /integrations/smtp/hooks/{hook_id}/deliveries/{delivery_id}/replay`
  - `POST /integrations/smtp/hooks/test`
- Legacy-compatible SMTP settings endpoints remain available: `GET /integrations/smtp/settings`, `PUT /integrations/smtp/settings`, and `POST /integrations/smtp/test`.

## Tagging Page (Admin)

- Global auto-tagging controls for built-in classification categories
- Tunable defaults:
  - enabled built-in category tags
  - minimum auto-tag confidence
  - secondary tag limit
- Custom rule management:
  - create/edit/delete rules
  - `contains` or `regex` match type
  - choose fields to inspect (`title`, `summary`, `article text`, `feed name`)
  - optional category requirements
  - all feeds or selected feeds
  - optional minimum classification confidence
- Rule preview shows current corpus matches before save
- Reapply tagging queues a background pass for recent items
- API calls:
  - `GET /tagging/settings`
  - `PUT /tagging/settings`
  - `POST /tagging/rules`
  - `PATCH /tagging/rules/{id}`
  - `DELETE /tagging/rules/{id}`
  - `POST /tagging/rules/preview`
  - `POST /tagging/reapply`

## API Tokens Page

- Create token form: name, expiry days, scopes CSV
- One-time display of created token secret
- Token inventory and revoke action
- Admin optional filter by `user_id`
- API calls:
  - `GET /tokens`
  - `POST /tokens`
  - `DELETE /tokens/{id}`

## Users Page (Admin)

- Create user form
- Search and edit user directory
- Expandable role definitions for `admin`, `analyst`, and `viewer`
- Editable fields per user:
  - role
  - active flag
  - optional password reset
- API calls:
  - `GET /users`
  - `POST /users`
  - `PATCH /users/{id}`

## Audit Logs Page (Admin)

- Filter by `action`
- Filter by `actor_user_id`
- Paginated log table
- Export filtered logs to JSON (`Export JSON`)
- API call:
  - `GET /audit-logs`
  - `GET /audit-logs/export`

## Access Rules

- Protected by authenticated route guard.
- Webhook analytics/list/history are available to authenticated users for their own webhooks.
- Webhook create/update/test/retry/delete additionally require operator access (`admin` or `analyst`) and write notification access.
- Admin-only pages additionally protected with `RoleRoute` (`roles=['admin']`).
- AI is a nested admin-only settings page at `/settings/ai`, with `/ai` redirecting there for backward compatibility.

# Settings Section

## Purpose

Centralized account, token, and admin operations.

## Navigation Items

Always visible:

- Account
- API Tokens
- Notifications

Admin-only:

- AI (`/settings/ai`) when enabled
- Tagging
- Users
- Audit Logs

Legacy route behavior:

- `/ai` redirects to `/settings/ai`
- `/settings` redirects to `/settings/account`

## Account Page

- User profile summary (`email`, `role`, `status`, `created`)
- Change password form
- API calls:
  - `GET /auth/me`
  - `POST /auth/change-password`

## Notifications Page

- Personal outbound webhook notifications for:
  - `rss_item_new`
  - `alert_match`
  - `feed_failing`
  - `webhook_failed`
  - `daily_digest`
- Saved webhook list with create/edit/delete
- Create, update, test, and retry actions are allowed for `admin` users when the target matches `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS`, or when `NOTIFICATION_WEBHOOK_ALLOW_ADMIN_UNRESTRICTED=true` is explicitly enabled.
- Analysts can only create, update, test, or retry when an admin configures `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS`, and the target matches that allowlist's scheme, host, port, and any optional path prefix. Plain host entries default to `https`, exact `host:port` or full URL prefix entries can allow non-default ports or tenant-scoped paths, and `*.suffix` does not include the apex `suffix`.
- Delete remains available to webhook owners with operator access so analysts can remove stale webhooks even when outbound egress is locked down.
- Viewers can still see their own notification analytics and delivery history when scopes permit.
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
- Notifications analytics/list/history are available to authenticated users for their own webhooks.
- Notifications delete additionally requires operator access.
- Notifications create/update/test/retry require `admin` plus an allowlisted target or explicit unrestricted-admin webhook mode; analysts always require a target approved by `NOTIFICATION_WEBHOOK_ALLOWED_HOSTS`.
- Admin-only pages additionally protected with `RoleRoute` (`roles=['admin']`).
- AI is a nested admin-only settings page at `/settings/ai`, with `/ai` redirecting there for backward compatibility.

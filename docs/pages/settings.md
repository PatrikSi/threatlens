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
- API calls:
  - `GET /auth/me`
  - `POST /auth/change-password`

## Integrations: Webhooks

- Personal outbound webhook notifications for:
  - `rss_item_new`
  - `alert_match`
  - `feed_failing`
  - `webhook_failed`
  - `daily_digest`
- Saved webhook list with create/edit/delete
- Create, update, test, retry, and delete actions are available to `admin` and `analyst` users with write notification access.
- Viewers can still see their own notification analytics and delivery history when scopes permit.
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

- Configurable outbound SMTP destination for notification email.
- Supports enabling/disabling, host, port, security mode, credentials, sender identity, timeout, event types, feed scope, subject template, and HTML template.
- Test tooling can run a connection/authentication check or send a rendered test email to a chosen recipient.
- API calls:
  - `GET /integrations/connectors`
  - `GET /integrations/smtp/settings`
  - `PUT /integrations/smtp/settings`
  - `POST /integrations/smtp/test`

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

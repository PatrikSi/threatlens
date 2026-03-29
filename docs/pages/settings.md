# Settings Section

## Purpose

Centralized account, token, and admin operations.

## Navigation Items

Always visible:

- Overview
- Account
- Notifications
- API Tokens

Admin-only:

- Tagging
- Users
- Audit Logs

Related top-level admin section:

- AI (`/ai`) when AI is enabled

## Overview

Role capabilities reference cards:

- Admin
- Analyst
- Viewer

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
- Notifications are available to all authenticated users for their own webhooks.
- Admin-only pages additionally protected with `RoleRoute` (`roles=['admin']`).
- AI is a separate admin-only top-nav section rather than a nested Settings page.

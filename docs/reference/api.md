# Backend API Reference

Base path is served at `/` on API service port `8000`. In the web app, requests are proxied through `/api`.

## Auth

### `GET /auth/registration-settings`

- Auth: none
- Response (`RegistrationSettingsResponse`):
  - `allow_self_registration`

### `POST /auth/register`

- Auth: none
- Config gate: `ALLOW_SELF_REGISTRATION` must be `true`
- Body (`RegisterRequest`):
  - `email`: valid email
  - `password`: string, `8..256`
- Response (`UserResponse`):
  - `id`, `email`, `role`
  - `is_active`, `is_approved`, `approved_at`
  - `created_at`

### `POST /auth/login`

- Auth: none
- Body (`LoginRequest`):
  - `email`
  - `password`
- Response (`TokenResponse`):
  - `access_token`
  - `token_type` (`bearer`)
  - `csrf_token` (for browser mutating requests when cookie auth is used)
- Side behavior:
  - Sets HttpOnly session cookie and CSRF cookie for browser-based auth.
- Rate limiting:
  - Returns `429` with `Retry-After` when failed-login thresholds are exceeded.

Example:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"admin123"}'
```

### `GET /auth/me`

- Auth: JWT or API token
- Response (`UserResponse`):
  - `id`, `email`, `role`
  - `is_active`, `is_approved`, `approved_at`
  - `created_at`

### `POST /auth/logout`

- Auth: none (best effort)
- Response: `{ "status": "ok" }`
- Side behavior:
  - Clears auth + CSRF cookies.

### `POST /auth/change-password`

- Auth: JWT or API token mapped user
- Body (`ChangePasswordRequest`):
  - `current_password`
  - `new_password` (`8..256`)
- Response: `{ "status": "ok" }`

## Feeds

### `GET /feeds`

- Auth: `read:feeds`
- Response: `FeedResponse[]`

### `POST /feeds/metadata`

- Auth: role `admin|analyst`, scope `read:feeds`
- Body (`FeedMetadataRequest`):
  - `url` (`5..4000`)
- Response (`FeedMetadataResponse`):
  - `name`, `description`, `site_url`, `language`
  - `etag`, `last_modified`, `resolved_url`, `feed_type`

### `GET /feeds/export`

- Auth: `read:feeds`
- Response (`FeedExportResponse`):
  - `exported_at`
  - `feeds`: array of `FeedImportEntry`

### `POST /feeds/import`

- Auth: role `admin|analyst`, scope `write:feeds`
- Body (`FeedImportRequest`):
  - `feeds`: `FeedImportEntry[]`
  - `overwrite_existing`: boolean
- Response (`FeedImportResponse`):
  - `created`, `updated`, `skipped`, `errors[]`
- Side behavior: queues asynchronous metadata backfill tasks for created/updated feeds.

### `POST /feeds`

- Auth: role `admin|analyst`, scope `write:feeds`
- Body (`FeedCreate`):
  - `name?` (`1..255`)
  - `url` (`5..4000`)
  - `description?`, `site_url?`, `language?`
  - `enabled`
  - `fetch_mode`: `interval|schedule`
  - `fetch_interval_seconds?` (`60..86400`, required in interval mode)
  - `schedule_cron?` (required + cron-valid in schedule mode)
- Response: `FeedResponse`
- Side behavior: queues an asynchronous metadata backfill task for the new feed.

### `PATCH /feeds/{feed_id}`

- Auth: role `admin|analyst`, scope `write:feeds`
- Body (`FeedUpdate`): partial updates for feed fields
- Response: `FeedResponse`

### `DELETE /feeds/{feed_id}`

- Auth: role `admin`, scope `write:feeds`
- Response: `204`

### `POST /feeds/{feed_id}/refresh`

- Auth: role `admin|analyst`, scope `write:feeds`
- Response: `{ "status": "queued" }` (`202`)

## Items

### `GET /items`

- Auth: `read:items`
- Query params:
  - `q?`: free text
  - `feed_id?`: UUID
  - `feed_ids?`: CSV UUID list
  - `tag?`: single tag
  - `tags?`: CSV tag list
  - `tags_mode`: `any|all` (default `any`)
  - `is_starred?`: boolean
  - `is_read?`: boolean
  - `since?`: datetime
  - `until?`: datetime
  - `page`: int >=1 (default `1`)
  - `page_size`: `1..100` (default `25`)
  - `sort`: `published_at_desc|published_at_asc|first_seen_desc|first_seen_asc` (fallback `published_at_desc`)
- Response (`ItemListResponse`):
  - `items[]` (`ItemListEntry`)
    - `tags[]`: legacy tag-name list
    - `tag_details[]`: rich tag links with `id`, `name`, `source`, `confidence`, `rules_version`
  - `total`, `page`, `page_size`

### `GET /items/{item_id}`

- Auth: `read:items`
- Response (`ItemDetailResponse`):
  - core item fields
  - optional `classification`
  - `tags[]` and `tag_details[]` metadata
  - `tag_suggestions[]` (feedback-adjusted suggestions with confidence/source)
  - optional `article`
  - user state: `is_read`, `is_starred`, `note`, `updated_at`

### `GET /items/{item_id}/tag-suggestions`

- Auth: `read:items`
- Response (`ItemTagSuggestionListResponse`):
  - `item_id`
  - `suggestions[]` with `name`, `source`, `confidence`, `rules_version`

### `GET /items/{item_id}/graph`

- Auth: `read:items`
- Query params:
  - `focus_node_id?`: `item:<uuid>` or `ioc:<uuid>`
  - `related_item_limit`: `1..60` (default `16`)
  - `ioc_limit`: `1..60` (default `18`)
  - `since_days`: `1..180` (default `30`)
- Response (`ItemGraphResponse`):
  - `nodes[]`
  - `edges[]`
  - `focus_node_id`
  - `root_item_id`

### `POST /items/{item_id}/read`

- Auth: role `admin|analyst`, scope `write:items`
- Body (`ReadUpdateRequest`): `is_read` boolean
- Response: `{ "status": "ok" }`

### `POST /items/{item_id}/star`

- Auth: role `admin|analyst`, scope `write:items`
- Body (`StarUpdateRequest`): `is_starred` boolean
- Response: `{ "status": "ok" }`

### `POST /items/{item_id}/note`

- Auth: role `admin|analyst`, scope `write:items`
- Body (`NoteUpdateRequest`): `note` string or null
- Response: `{ "status": "ok" }`

### `POST /items/{item_id}/tags`

- Auth: role `admin|analyst`, scope `write:items`
- Body (`ItemTagsUpdateRequest`): `tag_ids: UUID[]`
- Validation:
  - Duplicate tag IDs return `422`.
  - Unknown tag IDs return `422`.
- Side effects:
  - Item tag links are persisted with `source=manual`, `confidence=1.0`, `rules_version=manual:v1`.
  - Emits weak-label feedback signals (`manual_add` / `manual_remove`) for tuning.
- Response: `{ "status": "ok" }`

## Alerts

### `GET /alerts`

- Auth: `read:alerts`
- Query params:
  - `include_disabled`: boolean (default `true`)
- Response: `AlertInterestResponse[]`

### `POST /alerts`

- Auth: `write:alerts`
- Body (`AlertInterestCreate`):
  - `name` (`1..255`)
  - `category` (`1..64`)
  - `keywords` array (`1..64` entries)
  - `enabled`
- Response: `AlertInterestResponse`

### `PATCH /alerts/{alert_id}`

- Auth: `write:alerts`
- Body (`AlertInterestUpdate`): partial fields
- Response: `AlertInterestResponse`

### `DELETE /alerts/{alert_id}`

- Auth: `write:alerts`
- Response: `204`

### `POST /alerts/preview`

- Auth: `read:alerts` + `read:items`
- Body (`AlertInterestPreviewRequest`):
  - `name?` (`..255`)
  - `category` (`1..64`)
  - `keywords` array (`1..64` entries)
  - `limit` (`1..25`, default `5`)
- Response (`AlertMatchListResponse`):
  - `items[]` (`AlertMatchEntry`)
  - `total`, `page`, `page_size`
- Side behavior:
  - Evaluates an unsaved alert against the current corpus.
  - Uses `first_seen_desc` ordering and always returns page `1`.

Example:

```bash
curl -X POST http://localhost:8000/alerts/preview \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Microsoft Preview",
    "category": "vendor",
    "keywords": ["microsoft", "exchange", "entra id"],
    "limit": 5
  }'
```

### `GET /alerts/matches`

- Auth: `read:alerts` + `read:items`
- Query params:
  - `q?`, `is_starred?`, `is_read?`, `since?`, `until?`
  - `alert_ids?`: CSV UUID list
  - `categories?`: CSV category list
  - `include_disabled`: boolean (default `false`)
  - `page`: int >=1 (default `1`)
  - `page_size`: `1..100` (default `25`)
  - `sort`: same set as `/items` (fallback `published_at_desc`)
- Response (`AlertMatchListResponse`):
  - `items[]` (`AlertMatchEntry` = `ItemListEntry` + `matches[]`)
  - `total`, `page`, `page_size`

## Notifications

### `GET /notifications/template-variables`

- Auth: `read:notifications`
- Response (`NotificationTemplateVariable[]`):
  - `key`
  - `description`
  - `example`

### `GET /notifications/webhooks`

- Auth: `read:notifications`
- Response: current user webhooks (`NotificationWebhookResponse[]`)

### `POST /notifications/webhooks`

- Auth: `write:notifications`
- Body (`NotificationWebhookWrite`):
  - `name` (`1..255`)
  - `enabled`
  - `event_type`: currently `rss_item_new`
  - `url_template` (`5..4000`)
  - `method`: `GET|POST|PUT|PATCH|DELETE`
  - `feed_scope`: `all|selected`
  - `feed_ids[]`
  - `query_params[]`: `{ key, value }`
  - `headers[]`: `{ key, value }`
  - `body_mode`: `none|json|form|raw`
  - `body_fields[]`: `{ key, value }`
  - `body_template?`
  - `timeout_seconds` (`1..60`)
- Response: `NotificationWebhookResponse`
- Side behavior:
  - Query params embedded in `url_template` are normalized into `query_params`.
  - Only the authenticated user's webhook is created.

Example:

```bash
curl -X POST http://localhost:8000/notifications/webhooks \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Gotify",
    "enabled": true,
    "event_type": "rss_item_new",
    "url_template": "http://gotify.local/message?token=abc123",
    "method": "POST",
    "feed_scope": "all",
    "feed_ids": [],
    "query_params": [],
    "headers": [{"key":"Content-Type","value":"application/json"}],
    "body_mode": "raw",
    "body_fields": [],
    "body_template": "{\"title\":\"ThreatLens Alert\",\"message\":\"{{ item.title }}\",\"priority\":5}",
    "timeout_seconds": 10
  }'
```

### `PATCH /notifications/webhooks/{webhook_id}`

- Auth: `write:notifications`
- Body: same shape as `NotificationWebhookWrite`
- Response: `NotificationWebhookResponse`
- Behavior: only updates webhooks belonging to the authenticated user.

### `DELETE /notifications/webhooks/{webhook_id}`

- Auth: `write:notifications`
- Response: `204`
- Behavior: only deletes webhooks belonging to the authenticated user.

### `POST /notifications/webhooks/test`

- Auth: `write:notifications`
- Body (`NotificationWebhookTestRequest`):
  - `webhook`: `NotificationWebhookWrite`
  - `sample_feed_id?`: UUID
  - `sample_item_id?`: UUID
- Response (`NotificationWebhookTestResponse`):
  - `success`
  - `status_code`
  - `duration_ms`
  - `rendered_url`
  - `rendered_method`
  - `rendered_headers[]`
  - `rendered_query_params[]`
  - `rendered_body`
  - `response_body_preview`
  - `error`

### `GET /notifications/webhooks/{webhook_id}/deliveries`

- Auth: `read:notifications`
- Query params:
  - `page`: int >=1 (default `1`)
  - `page_size`: `1..100` (default `10`)
- Response (`NotificationWebhookDeliveryListResponse`):
  - `deliveries[]`
  - `total`, `page`, `page_size`
- Behavior: only lists deliveries for the authenticated user's webhook.

### `POST /notifications/webhooks/{webhook_id}/deliveries/{delivery_id}/retry`

- Auth: `write:notifications`
- Response (`NotificationWebhookDeliveryResponse`)
- Behavior:
  - Replays the stored rendered request snapshot for a past delivery.
  - Returns the new retry delivery row, not the original delivery.

## Tags

### `GET /tags`

- Auth: `read:tags`
- Response: `TagResponse[]`

### `POST /tags`

- Auth: role `admin|analyst`, scope `write:tags`
- Body (`TagCreate`):
  - `name` (`1..64`)
- Response: `TagResponse`

## Tagging

### `GET /tagging/settings`

- Auth: role `admin`, scope `read:tags`
- Response (`TaggingSettingsBundleResponse`):
  - `settings`
    - `enabled_categories[]`
    - `min_auto_tag_confidence`
    - `secondary_tag_limit`
  - `rules[]`

### `PUT /tagging/settings`

- Auth: role `admin`, scope `write:tags`
- Body (`TaggingSettingsUpdate`):
  - `enabled_categories[]`
  - `min_auto_tag_confidence` (`0.05..0.995`)
  - `secondary_tag_limit` (`0..2`)
- Response: `TaggingSettingsResponse`

### `POST /tagging/rules`

- Auth: role `admin`, scope `write:tags`
- Body (`TaggingRuleWrite`):
  - `name` (`1..255`)
  - `tag_name` (`1..64`)
  - `enabled`
  - `match_type`: `contains|regex`
  - `pattern` (`1..4000`)
  - `case_sensitive`
  - `applies_to[]`: `title|summary|article_text|feed_name`
  - `required_categories[]`
  - `feed_scope`: `all|selected`
  - `feed_ids[]`
  - `min_classification_confidence?` (`0..1`)
- Response: `TaggingRuleResponse`
- Side behavior:
  - Creates the tag row automatically if `tag_name` does not yet exist.
  - Regex rules are compiled and validated on create/update.

### `PATCH /tagging/rules/{rule_id}`

- Auth: role `admin`, scope `write:tags`
- Body: same shape as `TaggingRuleWrite`
- Response: `TaggingRuleResponse`

### `DELETE /tagging/rules/{rule_id}`

- Auth: role `admin`, scope `write:tags`
- Response: `204`

### `POST /tagging/rules/preview`

- Auth: role `admin`, scope `read:tags`
- Body (`TaggingRulePreviewRequest`):
  - all `TaggingRuleWrite` fields
  - `limit` (`1..25`, default `5`)
- Response (`TaggingRulePreviewResponse`):
  - `total`
  - `items[]`
    - `id`
    - `title`
    - `feed_name`
    - `classification`
    - `first_seen_at`
    - `current_tags[]`
    - `matched_sections[]`

Example:

```bash
curl -X POST http://localhost:8000/tagging/rules/preview \
  -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Fortinet Vendor",
    "tag_name": "vendor:fortinet",
    "enabled": true,
    "match_type": "contains",
    "pattern": "fortinet",
    "case_sensitive": false,
    "applies_to": ["title", "article_text"],
    "required_categories": ["vulnerability"],
    "feed_scope": "all",
    "feed_ids": [],
    "min_classification_confidence": 0.6,
    "limit": 5
  }'
```

### `POST /tagging/reapply`

- Auth: role `admin`, scope `write:tags`
- Body (`TaggingReapplyRequest`):
  - `days` (`1..365`, default `30`)
  - `limit` (`0..5000`, default `0`)
- Response (`TaggingReapplyResponse`):
  - `task_id`
  - `queued`
- Side behavior:
  - Queues a background Celery task to re-tag recent items with current settings and custom rules.

## Saved Views

### `GET /views`

- Auth: `read:views`
- Response: current user views (`SavedViewResponse[]`)

### `POST /views`

- Auth: `write:views`
- Body (`SavedViewCreate`):
  - `name` (`1..255`)
  - `query_json` object
- Response: `SavedViewResponse`

### `DELETE /views/{view_id}`

- Auth: `write:views`
- Behavior: only deletes views belonging to authenticated user
- Response: `204`

## API Tokens

### `GET /tokens`

- Auth: `read:tokens`
- Query params:
  - `user_id?`: UUID (admin-only override)
- Response: `ApiTokenResponse[]`

### `POST /tokens`

- Auth: `write:tokens`
- Body (`ApiTokenCreateRequest`):
  - `name` (`1..255`)
  - `expires_in_days?` (`1..3650`)
  - `scopes[]` (normalized lowercase; validated against allowed scope list)
- Response (`ApiTokenCreateResponse`):
  - `token` (shown once)
  - `token_prefix`
  - `expires_at`

### `DELETE /tokens/{token_id}`

- Auth: `write:tokens`
- Behavior: own token or admin can revoke
- Response: `204`

## Users

### `GET /users`

- Auth: role `admin`, scope `read:users`
- Response: `UserAdminResponse[]`

### `POST /users`

- Auth: role `admin`, scope `write:users`
- Body (`UserCreateRequest`):
  - `email`
  - `password` (`8..256`)
  - `role`: `admin|analyst|viewer`
  - `is_active`
- Response: `UserAdminResponse`

### `PATCH /users/{user_id}`

- Auth: role `admin`, scope `write:users`
- Body (`UserUpdateRequest`): partial update fields
- Response: `UserAdminResponse`

## Audit Logs

### `GET /audit-logs`

- Auth: role `admin`, scope `read:audit`
- Query params:
  - `action?`: exact action string
  - `actor_user_id?`: UUID
  - `page`: int >=1 (default `1`)
  - `page_size`: `1..200` (default `50`)
- Response (`AuditLogListResponse`):
  - `logs[]`
  - `total`, `page`, `page_size`

### `GET /audit-logs/export`

- Auth: role `admin`, scope `read:audit`
- Query params:
  - `action?`: exact action string
  - `actor_user_id?`: UUID
  - `limit`: `1..20000` (default `5000`)
- Response (`AuditLogExportResponse`):
  - `exported_at`
  - `total`: total matching rows before limit
  - `truncated`: `true` when `total > limit`
  - `logs[]`: newest-first export rows

## Stats

### `GET /stats/overview`

- Auth: `read:stats`
- Query params:
  - `days`: `7..365` (default `30`)
  - `feed_ids?`: CSV UUID list
- Response (`StatsOverviewResponse`):
  - `generated_at`, `window_days`
  - `totals`, `activity`, `derived`
  - `status_breakdown[]`, `daily_volume[]`, `feed_breakdown[]`, `top_domains[]`

### `GET /stats/feed-timeseries`

- Auth: `read:stats`
- Query params:
  - `days`: `7..365` (default `30`)
  - `feed_ids?`: CSV UUID list
  - `top_feeds`: `1..20` (default `8`)
- Response (`FeedTimeSeriesResponse`):
  - `generated_at`, `window_days`
  - `series[]` with per-day points grouped by `published_at` date

### `GET /stats/activity-heatmap`

- Auth: `read:stats`
- Query params:
  - `days`: `7..365` (default `30`)
  - `feed_ids?`: CSV UUID list
- Response (`ActivityHeatmapResponse`):
  - `generated_at`, `window_days`
  - `bucket_unit`: `hour` for `days<=7`, otherwise `day`
  - `bucket_labels[]`
  - `rows[]`: day rows with `day` and `counts[]` sized to `bucket_labels`
  - `max_count`

### `GET /stats/signal-radar`

- Auth: `read:stats`
- Query params:
  - `days`: `7..365` (default `30`)
  - `feed_ids?`: CSV UUID list
- Response (`SignalRadarResponse`):
  - `generated_at`, `window_days`
  - `total`: total classified items in the window
  - `max_count`: max category count in returned axes
  - `axes[]`:
    - `category`
    - `count`
    - `pct`

## Health

### `GET /health`

- Auth: none
- Response:
  - `ok`: boolean (`db` and `redis` both healthy)
  - `db`: boolean
  - `redis`: boolean
- Status code: `200` when healthy, `503` when not ready.

### `GET /health/ready`

- Auth: none
- Response: same shape as `/health`
- Status code: `200` when healthy, `503` when not ready.

### `GET /health/live`

- Auth: none
- Response: `{ "ok": true }`
- Status code: `200`

### `GET /health/worker`

- Auth: none
- Response:
  - `ok`: boolean (`true` when at least one worker responds to Celery ping)
  - `workers`: map of `worker_name -> pong status`
- Status code: `200` when healthy, `503` when no workers respond.

### `GET /health/beat`

- Auth: none
- Response:
  - `ok`: boolean (`true` when beat heartbeat is present and fresh)
  - `heartbeat_key`: redis key used for beat heartbeat
  - `heartbeat_at`: last heartbeat timestamp (ISO string) or `null`
  - `age_seconds`: heartbeat age in seconds or `null`
  - `stale_after_seconds`: freshness threshold
- Status code: `200` when healthy, `503` when stale/missing heartbeat.

## Error Patterns

Common error details emitted by handlers include:

- `Not authenticated` (`401`)
- `Invalid credentials` (`401`)
- `Insufficient permissions` (`403`)
- `Insufficient token scope` (`403`)
- `Account is inactive` (`403`)
- `... not found` (`404`)
- Validation-driven `422` messages for invalid UUIDs, cron expressions, feed URLs, and other schema constraints

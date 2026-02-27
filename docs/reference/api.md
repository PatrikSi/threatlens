# Backend API Reference

Base path is served at `/` on API service port `8000`. In the web app, requests are proxied through `/api`.

## Auth

### `POST /auth/register`

- Auth: none
- Config gate: `ALLOW_SELF_REGISTRATION` must be `true`
- Body (`RegisterRequest`):
  - `email`: valid email
  - `password`: string, `8..256`
- Response (`UserResponse`): `id`, `email`, `role`, `is_active`, `created_at`

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

### `GET /auth/me`

- Auth: JWT or API token
- Response: `UserResponse`

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

## Tags

### `GET /tags`

- Auth: `read:tags`
- Response: `TagResponse[]`

### `POST /tags`

- Auth: role `admin|analyst`, scope `write:tags`
- Body (`TagCreate`):
  - `name` (`1..64`)
- Response: `TagResponse`

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

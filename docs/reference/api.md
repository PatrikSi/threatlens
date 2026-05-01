# Backend API Reference

This file is generated from the live FastAPI OpenAPI schema. Do not edit it by hand.

## Published Contract

- Schema version: `0.1.0`
- OpenAPI contract anchor: `openapi-sha256:0c57bead937527ef519dea4811282a316279142b7bad552ae059027e872c12e0`
- API service base path: `/v1`
- Web proxy base path: `/api/v1`
- Bundled web proxy publishes only `/api/v1/*` plus `/api/openapi.json`.
- Any unversioned backend-service compatibility aliases are excluded from the published schema and shipped browser/runtime contract.
- Machine-readable OpenAPI schema on the API service: `/openapi.json`
- Machine-readable OpenAPI schema through the web proxy: `/api/openapi.json`

## Security Schemes

- `ApiTokenBearer`: `http` - Use a scoped personal API token in the `Authorization: Bearer <token>` header. Browser sign-in at `/v1/auth/login` creates a cookie session and returns only session-cookie metadata; bearer auth requires a dedicated API token.
- `SessionCookieAuth`: `apiKey` - HttpOnly browser session cookie established by `/v1/auth/login`, mirrored through the web proxy at `/api/v1/auth/login`. Cookie-authenticated mutating requests must also send the CSRF header.

## Ai

### `POST /v1/ai/daily-brief/generate`
- Summary: Generate Daily Brief
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Responses: `200` `application/json` -> AIDailyBriefResponse
### `GET /v1/ai/daily-brief/latest`
- Summary: Get Latest Daily Brief
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Responses: `200` `application/json` -> AIDailyBriefResponse
### `POST /v1/ai/daily-brief/queue`
- Summary: Queue Daily Brief
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Responses: `200` `application/json` -> AIQueuedTaskResponse
### `GET /v1/ai/daily-briefs`
- Summary: List Daily Briefs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `limit` (query, optional): Limit
- Responses: `200` `application/json` -> array[AIDailyBriefResponse], `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/daily-briefs/{brief_id}/sources`
- Summary: List Daily Brief Sources
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `brief_id` (path, required): string
  - `included` (query, optional): Included
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIDailyBriefSourceItemResponse], `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/live`
- Summary: Get Ai Ops Live
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Responses: `200` `application/json` -> AILiveStatusResponse
### `GET /v1/ai/ops/manual-actions`
- Summary: List Ai Ops Manual Actions
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIAuditEntryResponse], `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/overview`
- Summary: Get Ai Ops Overview
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `days` (query, optional): integer
- Responses: `200` `application/json` -> AIOpsOverviewResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/prompt-history`
- Summary: List Ai Ops Prompt History
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIAuditEntryResponse], `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/runs`
- Summary: List Ai Ops Runs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `limit` (query, optional): integer
  - `offset` (query, optional): integer
  - `days` (query, optional): Days
  - `task_type` (query, optional): Task Type
  - `status` (query, optional): Status
  - `trigger_source` (query, optional): Trigger Source
  - `model` (query, optional): Model
  - `parent_run_id` (query, optional): Parent Run Id
  - `only_failures` (query, optional): boolean
- Responses: `200` `application/json` -> AITaskRunListResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/runs/{run_id}`
- Summary: Get Ai Ops Run Detail
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `run_id` (path, required): string
- Responses: `200` `application/json` -> AITaskRunDetailResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/ai/ops/runs/{run_id}/cancel`
- Summary: Cancel Ai Ops Run
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Parameters:
  - `run_id` (path, required): string
- Responses: `200` `application/json` -> AITaskRunResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/ai/reprocess`
- Summary: Reprocess Ai For Recent Items
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Request body: `application/json` -> AIReprocessRequest
- Responses: `200` `application/json` -> AIReprocessResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/settings`
- Summary: Get Ai Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Responses: `200` `application/json` -> AISettingsResponse
### `PUT /v1/ai/settings`
- Summary: Update Ai Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Request body: `application/json` -> AISettingsUpdate
- Responses: `200` `application/json` -> AISettingsResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/ai/test-connection`
- Summary: Test Ai Connection
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Responses: `200` `application/json` -> AITestConnectionResponse
### `GET /v1/ai/usage`
- Summary: Get Ai Usage
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Responses: `200` `application/json` -> AIUsageSummaryResponse

## Alerts

### `GET /v1/alerts`
- Summary: List Alert Interests
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`
- Parameters:
  - `include_disabled` (query, optional): boolean
- Responses: `200` `application/json` -> array[AlertInterestResponse], `422` `application/json` -> HTTPValidationError
### `POST /v1/alerts`
- Summary: Create Alert Interest
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`
- Request body: `application/json` -> AlertInterestCreate
- Responses: `201` `application/json` -> AlertInterestResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/alerts/matches`
- Summary: List Alert Matches
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`, `read:items`
- Parameters:
  - `q` (query, optional): Q
  - `alert_ids` (query, optional): Alert Ids
  - `categories` (query, optional): Categories
  - `include_disabled` (query, optional): boolean
  - `is_starred` (query, optional): Is Starred
  - `is_read` (query, optional): Is Read
  - `since` (query, optional): Since
  - `until` (query, optional): Until
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
  - `sort` (query, optional): string
- Responses: `200` `application/json` -> AlertMatchListResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/alerts/preview`
- Summary: Preview Alert Interest
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`, `read:items`
- Request body: `application/json` -> AlertInterestPreviewRequest
- Responses: `200` `application/json` -> AlertMatchListResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/alerts/{alert_id}`
- Summary: Delete Alert Interest
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`
- Parameters:
  - `alert_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/alerts/{alert_id}`
- Summary: Update Alert Interest
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`
- Parameters:
  - `alert_id` (path, required): string
- Request body: `application/json` -> AlertInterestUpdate
- Responses: `200` `application/json` -> AlertInterestResponse, `422` `application/json` -> HTTPValidationError

## Audit

### `GET /v1/audit-logs`
- Summary: List Audit Logs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:audit`
- Parameters:
  - `action` (query, optional): Action
  - `actor_user_id` (query, optional): Actor User Id
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> AuditLogListResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/audit-logs/export`
- Summary: Export Audit Logs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:audit`
- Parameters:
  - `action` (query, optional): Action
  - `actor_user_id` (query, optional): Actor User Id
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> AuditLogExportResponse, `422` `application/json` -> HTTPValidationError

## Auth

### `POST /v1/auth/change-password`
- Summary: Change Password
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> ChangePasswordRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `POST /v1/auth/login`
- Summary: Login
- Auth: none
- Request body: `application/json` -> LoginRequest
- Responses: `200` `application/json` -> TokenResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/auth/logout`
- Summary: Logout
- Auth: none
- Responses: `200` `application/json` -> unspecified
### `GET /v1/auth/me`
- Summary: Me
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> CurrentUserResponse
### `POST /v1/auth/register`
- Summary: Register
- Auth: none
- Request body: `application/json` -> RegisterRequest
- Responses: `200` `application/json` -> UserResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/auth/registration-settings`
- Summary: Registration Settings
- Auth: none
- Responses: `200` `application/json` -> RegistrationSettingsResponse

## Feeds

### `GET /v1/feeds`
- Summary: List Feeds
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:feeds`
- Responses: `200` `application/json` -> array[FeedResponse]
### `POST /v1/feeds`
- Summary: Create Feed
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Request body: `application/json` -> FeedCreate
- Responses: `201` `application/json` -> FeedResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/feeds/export`
- Summary: Export Feeds Sanitized
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:feeds`
- Responses: `200` `application/json` -> FeedExportResponse
### `GET /v1/feeds/export/backup`
- Summary: Export Feeds Backup
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Responses: `200` `application/json` -> FeedExportResponse
### `POST /v1/feeds/import`
- Summary: Import Feeds
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Request body: `application/json` -> FeedImportRequest
- Responses: `200` `application/json` -> FeedImportResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/feeds/metadata`
- Summary: Get Feed Metadata
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:feeds`
- Request body: `application/json` -> FeedMetadataRequest
- Responses: `200` `application/json` -> FeedMetadataResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/feeds/{feed_id}`
- Summary: Delete Feed
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Parameters:
  - `feed_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/feeds/{feed_id}`
- Summary: Update Feed
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Parameters:
  - `feed_id` (path, required): string
- Request body: `application/json` -> FeedUpdate
- Responses: `200` `application/json` -> FeedResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/feeds/{feed_id}/refresh`
- Summary: Refresh Feed
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Parameters:
  - `feed_id` (path, required): string
- Responses: `202` `application/json` -> unspecified, `404`, `422` `application/json` -> HTTPValidationError, `503`

## Health

### `GET /v1/health`
- Summary: Health
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/beat`
- Summary: Beat
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/encrypted-data`
- Summary: Encrypted Data
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> EncryptedDataInventoryResponse
### `GET /v1/health/live`
- Summary: Live
- Auth: none
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/notifications`
- Summary: Notifications
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/ready`
- Summary: Ready
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/worker`
- Summary: Worker
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> unspecified

## Items

### `GET /v1/items`
- Summary: List Items
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `q` (query, optional): Q
  - `feed_id` (query, optional): Feed Id
  - `feed_ids` (query, optional): Feed Ids
  - `tag` (query, optional): Tag
  - `tags` (query, optional): Tags
  - `tags_mode` (query, optional): string
  - `is_starred` (query, optional): Is Starred
  - `is_read` (query, optional): Is Read
  - `since` (query, optional): Since
  - `until` (query, optional): Until
  - `has_article` (query, optional): Has Article
  - `date_basis` (query, optional): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
  - `sort` (query, optional): string
- Responses: `200` `application/json` -> ItemListResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/items/{item_id}`
- Summary: Get Item
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `item_id` (path, required): string
- Responses: `200` `application/json` -> ItemDetailResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/items/{item_id}/article-preview`
- Summary: Get Item Article Preview
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `item_id` (path, required): string
- Responses: `200` `text/html` -> string, `422` `application/json` -> HTTPValidationError
### `GET /v1/items/{item_id}/graph`
- Summary: Get Item Graph
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `item_id` (path, required): string
  - `focus_node_id` (query, optional): Focus Node Id
  - `related_item_limit` (query, optional): integer
  - `ioc_limit` (query, optional): integer
  - `since_days` (query, optional): integer
- Responses: `200` `application/json` -> ItemGraphResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/note`
- Summary: Set Item Note
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> NoteUpdateRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/read`
- Summary: Set Item Read
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> ReadUpdateRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/retry-article-fetch`
- Summary: Retry Item Article Fetch
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Responses: `202` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/star`
- Summary: Set Item Star
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> StarUpdateRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `GET /v1/items/{item_id}/tag-suggestions`
- Summary: Get Item Tag Suggestions
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `item_id` (path, required): string
- Responses: `200` `application/json` -> ItemTagSuggestionListResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/tags`
- Summary: Set Item Tags
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> ItemTagsUpdateRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError

## Notifications

### `GET /v1/notifications/analytics`
- Summary: Get Notifications Analytics
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Responses: `200` `application/json` -> NotificationAnalyticsResponse
### `GET /v1/notifications/template-variables`
- Summary: Get Notification Template Variables
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Responses: `200` `application/json` -> array[NotificationTemplateVariable]
### `GET /v1/notifications/webhook-policy`
- Summary: Get Notification Webhook Policy
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Responses: `200` `application/json` -> NotificationWebhookPolicyResponse
### `GET /v1/notifications/webhooks`
- Summary: List Notification Webhooks
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Responses: `200` `application/json` -> array[NotificationWebhookResponse]
### `POST /v1/notifications/webhooks`
- Summary: Create Notification Webhook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Request body: `application/json` -> NotificationWebhookWrite
- Responses: `201` `application/json` -> NotificationWebhookResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/notifications/webhooks/test`
- Summary: Test Notification Webhook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Request body: `application/json` -> NotificationWebhookTestRequest
- Responses: `200` `application/json` -> NotificationWebhookTestResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/notifications/webhooks/{webhook_id}`
- Summary: Delete Notification Webhook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Parameters:
  - `webhook_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/notifications/webhooks/{webhook_id}`
- Summary: Update Notification Webhook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Parameters:
  - `webhook_id` (path, required): string
- Request body: `application/json` -> NotificationWebhookWrite
- Responses: `200` `application/json` -> NotificationWebhookResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/notifications/webhooks/{webhook_id}/deliveries`
- Summary: List Notification Webhook Deliveries
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Parameters:
  - `webhook_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> NotificationWebhookDeliveryListResponse, `404`, `422`
### `POST /v1/notifications/webhooks/{webhook_id}/deliveries/{delivery_id}/retry`
- Summary: Retry Notification Webhook Delivery
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Parameters:
  - `webhook_id` (path, required): string
  - `delivery_id` (path, required): string
- Responses: `200` `application/json` -> NotificationWebhookDeliveryResponse, `404`, `409`, `422`

## Stats

### `GET /v1/stats/activity-heatmap`
- Summary: Get Activity Heatmap
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:stats`
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> ActivityHeatmapResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/stats/feed-timeseries`
- Summary: Get Feed Timeseries
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:stats`
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
  - `top_feeds` (query, optional): Top Feeds
- Responses: `200` `application/json` -> FeedTimeSeriesResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/stats/overview`
- Summary: Get Stats Overview
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:stats`
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> StatsOverviewResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/stats/signal-radar`
- Summary: Get Signal Radar
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:stats`
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> SignalRadarResponse, `422` `application/json` -> HTTPValidationError

## Tagging

### `POST /v1/tagging/reapply`
- Summary: Queue Tagging Reapply
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tags`
- Request body: `application/json` -> TaggingReapplyRequest
- Responses: `200` `application/json` -> TaggingReapplyResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/tagging/rules`
- Summary: Create Tagging Rule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tags`
- Request body: `application/json` -> TaggingRuleWrite
- Responses: `201` `application/json` -> TaggingRuleResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/tagging/rules/preview`
- Summary: Preview Tagging Rule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tags`
- Request body: `application/json` -> TaggingRulePreviewRequest
- Responses: `200` `application/json` -> TaggingRulePreviewResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/tagging/rules/{rule_id}`
- Summary: Delete Tagging Rule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tags`
- Parameters:
  - `rule_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/tagging/rules/{rule_id}`
- Summary: Update Tagging Rule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tags`
- Parameters:
  - `rule_id` (path, required): string
- Request body: `application/json` -> TaggingRuleWrite
- Responses: `200` `application/json` -> TaggingRuleResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/tagging/settings`
- Summary: Get Tagging Settings Bundle
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tags`
- Responses: `200` `application/json` -> TaggingSettingsBundleResponse
### `PUT /v1/tagging/settings`
- Summary: Update Tagging Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tags`
- Request body: `application/json` -> TaggingSettingsUpdate
- Responses: `200` `application/json` -> TaggingSettingsResponse, `422` `application/json` -> HTTPValidationError

## Tags

### `GET /v1/tags`
- Summary: List Tags
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tags`
- Responses: `200` `application/json` -> array[TagResponse]
### `POST /v1/tags`
- Summary: Create Tag
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tags`
- Request body: `application/json` -> TagCreate
- Responses: `201` `application/json` -> TagResponse, `422` `application/json` -> HTTPValidationError

## Tokens

### `GET /v1/tokens`
- Summary: List Tokens
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tokens`
- Parameters:
  - `user_id` (query, optional): User Id
- Responses: `200` `application/json` -> array[ApiTokenResponse], `422` `application/json` -> HTTPValidationError
### `POST /v1/tokens`
- Summary: Create Token
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tokens`
- Request body: `application/json` -> ApiTokenCreateRequest
- Responses: `201` `application/json` -> ApiTokenCreateResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/tokens/{token_id}`
- Summary: Revoke Token
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tokens`
- Parameters:
  - `token_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError

## Users

### `GET /v1/users`
- Summary: List Users
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:users`
- Responses: `200` `application/json` -> array[UserAdminResponse]
### `POST /v1/users`
- Summary: Create User
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:users`
- Request body: `application/json` -> UserCreateRequest
- Responses: `201` `application/json` -> UserAdminResponse, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/users/{user_id}`
- Summary: Update User
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:users`
- Parameters:
  - `user_id` (path, required): string
- Request body: `application/json` -> UserUpdateRequest
- Responses: `200` `application/json` -> UserAdminResponse, `422` `application/json` -> HTTPValidationError

## Views

### `GET /v1/views`
- Summary: List Views
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:views`
- Responses: `200` `application/json` -> array[SavedViewResponse]
### `POST /v1/views`
- Summary: Create View
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:views`
- Request body: `application/json` -> SavedViewCreate
- Responses: `201` `application/json` -> SavedViewResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/views/{view_id}`
- Summary: Delete View
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:views`
- Parameters:
  - `view_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/views/{view_id}`
- Summary: Update View
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:views`
- Parameters:
  - `view_id` (path, required): string
- Request body: `application/json` -> SavedViewUpdate
- Responses: `200` `application/json` -> SavedViewResponse, `422` `application/json` -> HTTPValidationError

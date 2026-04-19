# Backend API Reference

This file is generated from the live FastAPI OpenAPI schema. Do not edit it by hand.

## Published Contract

- API service base path: `/v1`
- Web proxy base path: `/api/v1`
- Legacy unversioned endpoints remain available for compatibility but are excluded from the published schema.
- Machine-readable OpenAPI schema on the API service: `/openapi.json`
- Machine-readable OpenAPI schema through the web proxy: `/api/openapi.json`

## Security Schemes

- `OAuth2PasswordBearer`: `oauth2`

## Ai

### `POST /v1/ai/daily-brief/generate`
- Summary: Generate Daily Brief
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> AIDailyBriefResponse
### `GET /v1/ai/daily-brief/latest`
- Summary: Get Latest Daily Brief
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> AIDailyBriefResponse
### `POST /v1/ai/daily-brief/queue`
- Summary: Queue Daily Brief
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> AIQueuedTaskResponse
### `GET /v1/ai/daily-briefs`
- Summary: List Daily Briefs
- Auth: OAuth2PasswordBearer
- Parameters:
  - `limit` (query, optional): Limit
- Responses: `200` `application/json` -> array[AIDailyBriefResponse], `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/daily-briefs/{brief_id}/sources`
- Summary: List Daily Brief Sources
- Auth: OAuth2PasswordBearer
- Parameters:
  - `brief_id` (path, required): string
  - `included` (query, optional): Included
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIDailyBriefSourceItemResponse], `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/live`
- Summary: Get Ai Ops Live
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> AILiveStatusResponse
### `GET /v1/ai/ops/manual-actions`
- Summary: List Ai Ops Manual Actions
- Auth: OAuth2PasswordBearer
- Parameters:
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIAuditEntryResponse], `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/overview`
- Summary: Get Ai Ops Overview
- Auth: OAuth2PasswordBearer
- Parameters:
  - `days` (query, optional): integer
- Responses: `200` `application/json` -> AIOpsOverviewResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/prompt-history`
- Summary: List Ai Ops Prompt History
- Auth: OAuth2PasswordBearer
- Parameters:
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIAuditEntryResponse], `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/ops/runs`
- Summary: List Ai Ops Runs
- Auth: OAuth2PasswordBearer
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
- Auth: OAuth2PasswordBearer
- Parameters:
  - `run_id` (path, required): string
- Responses: `200` `application/json` -> AITaskRunDetailResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/ai/ops/runs/{run_id}/cancel`
- Summary: Cancel Ai Ops Run
- Auth: OAuth2PasswordBearer
- Parameters:
  - `run_id` (path, required): string
- Responses: `200` `application/json` -> AITaskRunResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/ai/reprocess`
- Summary: Reprocess Ai For Recent Items
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> AIReprocessRequest
- Responses: `200` `application/json` -> AIReprocessResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/ai/settings`
- Summary: Get Ai Settings
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> AISettingsResponse
### `PUT /v1/ai/settings`
- Summary: Update Ai Settings
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> AISettingsUpdate
- Responses: `200` `application/json` -> AISettingsResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/ai/test-connection`
- Summary: Test Ai Connection
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> AITestConnectionResponse
### `GET /v1/ai/usage`
- Summary: Get Ai Usage
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> AIUsageSummaryResponse

## Alerts

### `GET /v1/alerts`
- Summary: List Alert Interests
- Auth: OAuth2PasswordBearer
- Parameters:
  - `include_disabled` (query, optional): boolean
- Responses: `200` `application/json` -> array[AlertInterestResponse], `422` `application/json` -> HTTPValidationError
### `POST /v1/alerts`
- Summary: Create Alert Interest
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> AlertInterestCreate
- Responses: `201` `application/json` -> AlertInterestResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/alerts/matches`
- Summary: List Alert Matches
- Auth: OAuth2PasswordBearer
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
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> AlertInterestPreviewRequest
- Responses: `200` `application/json` -> AlertMatchListResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/alerts/{alert_id}`
- Summary: Delete Alert Interest
- Auth: OAuth2PasswordBearer
- Parameters:
  - `alert_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/alerts/{alert_id}`
- Summary: Update Alert Interest
- Auth: OAuth2PasswordBearer
- Parameters:
  - `alert_id` (path, required): string
- Request body: `application/json` -> AlertInterestUpdate
- Responses: `200` `application/json` -> AlertInterestResponse, `422` `application/json` -> HTTPValidationError

## Audit

### `GET /v1/audit-logs`
- Summary: List Audit Logs
- Auth: OAuth2PasswordBearer
- Parameters:
  - `action` (query, optional): Action
  - `actor_user_id` (query, optional): Actor User Id
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> AuditLogListResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/audit-logs/export`
- Summary: Export Audit Logs
- Auth: OAuth2PasswordBearer
- Parameters:
  - `action` (query, optional): Action
  - `actor_user_id` (query, optional): Actor User Id
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> AuditLogExportResponse, `422` `application/json` -> HTTPValidationError

## Auth

### `POST /v1/auth/change-password`
- Summary: Change Password
- Auth: OAuth2PasswordBearer
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
- Auth: OAuth2PasswordBearer
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
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> array[FeedResponse]
### `POST /v1/feeds`
- Summary: Create Feed
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> FeedCreate
- Responses: `201` `application/json` -> FeedResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/feeds/export`
- Summary: Export Feeds
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> FeedExportResponse
### `POST /v1/feeds/import`
- Summary: Import Feeds
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> FeedImportRequest
- Responses: `200` `application/json` -> FeedImportResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/feeds/metadata`
- Summary: Get Feed Metadata
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> FeedMetadataRequest
- Responses: `200` `application/json` -> FeedMetadataResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/feeds/{feed_id}`
- Summary: Delete Feed
- Auth: OAuth2PasswordBearer
- Parameters:
  - `feed_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/feeds/{feed_id}`
- Summary: Update Feed
- Auth: OAuth2PasswordBearer
- Parameters:
  - `feed_id` (path, required): string
- Request body: `application/json` -> FeedUpdate
- Responses: `200` `application/json` -> FeedResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/feeds/{feed_id}/refresh`
- Summary: Refresh Feed
- Auth: OAuth2PasswordBearer
- Parameters:
  - `feed_id` (path, required): string
- Responses: `202` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError

## Health

### `GET /v1/health`
- Summary: Health
- Auth: none
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/beat`
- Summary: Beat
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/live`
- Summary: Live
- Auth: none
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/notifications`
- Summary: Notifications
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/ready`
- Summary: Ready
- Auth: none
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/worker`
- Summary: Worker
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> unspecified

## Items

### `GET /v1/items`
- Summary: List Items
- Auth: OAuth2PasswordBearer
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
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
  - `sort` (query, optional): string
- Responses: `200` `application/json` -> ItemListResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/items/{item_id}`
- Summary: Get Item
- Auth: OAuth2PasswordBearer
- Parameters:
  - `item_id` (path, required): string
- Responses: `200` `application/json` -> ItemDetailResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/items/{item_id}/graph`
- Summary: Get Item Graph
- Auth: OAuth2PasswordBearer
- Parameters:
  - `item_id` (path, required): string
  - `focus_node_id` (query, optional): Focus Node Id
  - `related_item_limit` (query, optional): integer
  - `ioc_limit` (query, optional): integer
  - `since_days` (query, optional): integer
- Responses: `200` `application/json` -> ItemGraphResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/note`
- Summary: Set Item Note
- Auth: OAuth2PasswordBearer
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> NoteUpdateRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/read`
- Summary: Set Item Read
- Auth: OAuth2PasswordBearer
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> ReadUpdateRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/retry-article-fetch`
- Summary: Retry Item Article Fetch
- Auth: OAuth2PasswordBearer
- Parameters:
  - `item_id` (path, required): string
- Responses: `202` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/star`
- Summary: Set Item Star
- Auth: OAuth2PasswordBearer
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> StarUpdateRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError
### `GET /v1/items/{item_id}/tag-suggestions`
- Summary: Get Item Tag Suggestions
- Auth: OAuth2PasswordBearer
- Parameters:
  - `item_id` (path, required): string
- Responses: `200` `application/json` -> ItemTagSuggestionListResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/items/{item_id}/tags`
- Summary: Set Item Tags
- Auth: OAuth2PasswordBearer
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> ItemTagsUpdateRequest
- Responses: `200` `application/json` -> unspecified, `422` `application/json` -> HTTPValidationError

## Notifications

### `GET /v1/notifications/analytics`
- Summary: Get Notifications Analytics
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> NotificationAnalyticsResponse
### `GET /v1/notifications/template-variables`
- Summary: Get Notification Template Variables
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> array[NotificationTemplateVariable]
### `GET /v1/notifications/webhooks`
- Summary: List Notification Webhooks
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> array[NotificationWebhookResponse]
### `POST /v1/notifications/webhooks`
- Summary: Create Notification Webhook
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> NotificationWebhookWrite
- Responses: `201` `application/json` -> NotificationWebhookResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/notifications/webhooks/test`
- Summary: Test Notification Webhook
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> NotificationWebhookTestRequest
- Responses: `200` `application/json` -> NotificationWebhookTestResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/notifications/webhooks/{webhook_id}`
- Summary: Delete Notification Webhook
- Auth: OAuth2PasswordBearer
- Parameters:
  - `webhook_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/notifications/webhooks/{webhook_id}`
- Summary: Update Notification Webhook
- Auth: OAuth2PasswordBearer
- Parameters:
  - `webhook_id` (path, required): string
- Request body: `application/json` -> NotificationWebhookWrite
- Responses: `200` `application/json` -> NotificationWebhookResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/notifications/webhooks/{webhook_id}/deliveries`
- Summary: List Notification Webhook Deliveries
- Auth: OAuth2PasswordBearer
- Parameters:
  - `webhook_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> NotificationWebhookDeliveryListResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/notifications/webhooks/{webhook_id}/deliveries/{delivery_id}/retry`
- Summary: Retry Notification Webhook Delivery
- Auth: OAuth2PasswordBearer
- Parameters:
  - `webhook_id` (path, required): string
  - `delivery_id` (path, required): string
- Responses: `200` `application/json` -> NotificationWebhookDeliveryResponse, `422` `application/json` -> HTTPValidationError

## Stats

### `GET /v1/stats/activity-heatmap`
- Summary: Get Activity Heatmap
- Auth: OAuth2PasswordBearer
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> ActivityHeatmapResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/stats/feed-timeseries`
- Summary: Get Feed Timeseries
- Auth: OAuth2PasswordBearer
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
  - `top_feeds` (query, optional): Top Feeds
- Responses: `200` `application/json` -> FeedTimeSeriesResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/stats/overview`
- Summary: Get Stats Overview
- Auth: OAuth2PasswordBearer
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> StatsOverviewResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/stats/signal-radar`
- Summary: Get Signal Radar
- Auth: OAuth2PasswordBearer
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> SignalRadarResponse, `422` `application/json` -> HTTPValidationError

## Tagging

### `POST /v1/tagging/reapply`
- Summary: Queue Tagging Reapply
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> TaggingReapplyRequest
- Responses: `200` `application/json` -> TaggingReapplyResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/tagging/rules`
- Summary: Create Tagging Rule
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> TaggingRuleWrite
- Responses: `201` `application/json` -> TaggingRuleResponse, `422` `application/json` -> HTTPValidationError
### `POST /v1/tagging/rules/preview`
- Summary: Preview Tagging Rule
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> TaggingRulePreviewRequest
- Responses: `200` `application/json` -> TaggingRulePreviewResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/tagging/rules/{rule_id}`
- Summary: Delete Tagging Rule
- Auth: OAuth2PasswordBearer
- Parameters:
  - `rule_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/tagging/rules/{rule_id}`
- Summary: Update Tagging Rule
- Auth: OAuth2PasswordBearer
- Parameters:
  - `rule_id` (path, required): string
- Request body: `application/json` -> TaggingRuleWrite
- Responses: `200` `application/json` -> TaggingRuleResponse, `422` `application/json` -> HTTPValidationError
### `GET /v1/tagging/settings`
- Summary: Get Tagging Settings Bundle
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> TaggingSettingsBundleResponse
### `PUT /v1/tagging/settings`
- Summary: Update Tagging Settings
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> TaggingSettingsUpdate
- Responses: `200` `application/json` -> TaggingSettingsResponse, `422` `application/json` -> HTTPValidationError

## Tags

### `GET /v1/tags`
- Summary: List Tags
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> array[TagResponse]
### `POST /v1/tags`
- Summary: Create Tag
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> TagCreate
- Responses: `201` `application/json` -> TagResponse, `422` `application/json` -> HTTPValidationError

## Tokens

### `GET /v1/tokens`
- Summary: List Tokens
- Auth: OAuth2PasswordBearer
- Parameters:
  - `user_id` (query, optional): User Id
- Responses: `200` `application/json` -> array[ApiTokenResponse], `422` `application/json` -> HTTPValidationError
### `POST /v1/tokens`
- Summary: Create Token
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> ApiTokenCreateRequest
- Responses: `201` `application/json` -> ApiTokenCreateResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/tokens/{token_id}`
- Summary: Revoke Token
- Auth: OAuth2PasswordBearer
- Parameters:
  - `token_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError

## Users

### `GET /v1/users`
- Summary: List Users
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> array[UserAdminResponse]
### `POST /v1/users`
- Summary: Create User
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> UserCreateRequest
- Responses: `201` `application/json` -> UserAdminResponse, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/users/{user_id}`
- Summary: Update User
- Auth: OAuth2PasswordBearer
- Parameters:
  - `user_id` (path, required): string
- Request body: `application/json` -> UserUpdateRequest
- Responses: `200` `application/json` -> UserAdminResponse, `422` `application/json` -> HTTPValidationError

## Views

### `GET /v1/views`
- Summary: List Views
- Auth: OAuth2PasswordBearer
- Responses: `200` `application/json` -> array[SavedViewResponse]
### `POST /v1/views`
- Summary: Create View
- Auth: OAuth2PasswordBearer
- Request body: `application/json` -> SavedViewCreate
- Responses: `201` `application/json` -> SavedViewResponse, `422` `application/json` -> HTTPValidationError
### `DELETE /v1/views/{view_id}`
- Summary: Delete View
- Auth: OAuth2PasswordBearer
- Parameters:
  - `view_id` (path, required): string
- Responses: `204`, `422` `application/json` -> HTTPValidationError
### `PATCH /v1/views/{view_id}`
- Summary: Update View
- Auth: OAuth2PasswordBearer
- Parameters:
  - `view_id` (path, required): string
- Request body: `application/json` -> SavedViewUpdate
- Responses: `200` `application/json` -> SavedViewResponse, `422` `application/json` -> HTTPValidationError

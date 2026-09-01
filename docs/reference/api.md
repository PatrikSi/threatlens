# Backend API Reference

This file is generated from the live FastAPI OpenAPI schema. Do not edit it by hand.

## Published Contract

- Schema version: `1.9.0`
- OpenAPI contract anchor: `openapi-sha256:74b999382541028b18aaa4dbc316c4d89f69a2c3f22f1917b428739c5b6a3566`
- API service base path: `/v1`
- Web proxy base path: `/api/v1`
- Bundled web proxy publishes only `/api/v1/*` plus `/api/openapi.json`.
- Any unversioned backend-service compatibility aliases are excluded from the published schema and shipped browser/runtime contract.
- Machine-readable OpenAPI schema on the API service: `/openapi.json`
- Machine-readable OpenAPI schema through the web proxy: `/api/openapi.json`

## Security Schemes

- `ApiTokenBearer`: `http` - Use a scoped personal API token or a `tlsa_` service-account token in the `Authorization: Bearer <token>` header. Service-account tokens are accepted only from this header and only on explicitly supported data-plane routes. Browser sign-in at `/v1/auth/login` creates a cookie session and returns only session-cookie metadata; bearer auth requires a dedicated API token.
- `SessionCookieAuth`: `apiKey` - HttpOnly browser session cookie established by `/v1/auth/login`, mirrored through the web proxy at `/api/v1/auth/login`. Cookie-authenticated mutating requests must also send the CSRF header.

## Error Diagnostics

Error responses retain FastAPI's top-level `detail` field for compatibility and also include an `error` object with a stable category in `code`, a display-safe `message`, the HTTP `status`, a `retryable` hint, and a correlation `request_id`. The same correlation value is returned in the `X-Request-ID` response header and can be used to locate the server-side log entry. Validation responses do not echo submitted input values.

## Access Reviews

### `GET /v1/iam/access-reviews`
- Summary: Get Access Review Campaigns
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:access_reviews`
- Parameters:
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
  - `status` (query, optional): Status
- Responses: `200` `application/json` -> AccessReviewCampaignListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/access-reviews`
- Summary: Post Access Review Campaign
- Auth: SessionCookieAuth
- Parameters:
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> AccessReviewCampaignCreate
- Responses: `201` `application/json` -> AccessReviewCampaignResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/access-reviews/{campaign_id}`
- Summary: Get Access Review Campaign
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:access_reviews`
- Parameters:
  - `campaign_id` (path, required): string
- Responses: `200` `application/json` -> AccessReviewCampaignResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/access-reviews/{campaign_id}/apply/complete`
- Summary: Post Access Review Apply Complete
- Auth: SessionCookieAuth
- Parameters:
  - `campaign_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> AccessReviewBeginApplyRequest
- Responses: `200` `application/json` -> AccessReviewCampaignResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/access-reviews/{campaign_id}/apply/items/{item_id}`
- Summary: Post Access Review Apply Item
- Auth: SessionCookieAuth
- Parameters:
  - `campaign_id` (path, required): string
  - `item_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> AccessReviewApplyItemRequest
- Responses: `200` `application/json` -> AccessReviewApplyReceiptResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/access-reviews/{campaign_id}/apply/items/{item_id}/resolve`
- Summary: Post Access Review Resolve Item
- Auth: SessionCookieAuth
- Parameters:
  - `campaign_id` (path, required): string
  - `item_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> AccessReviewResolveItemRequest
- Responses: `200` `application/json` -> AccessReviewApplyReceiptResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/access-reviews/{campaign_id}/apply/start`
- Summary: Post Access Review Apply Start
- Auth: SessionCookieAuth
- Parameters:
  - `campaign_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> AccessReviewBeginApplyRequest
- Responses: `200` `application/json` -> AccessReviewCampaignResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/access-reviews/{campaign_id}/cancel`
- Summary: Post Access Review Cancel
- Auth: SessionCookieAuth
- Parameters:
  - `campaign_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> AccessReviewTransitionRequest
- Responses: `200` `application/json` -> AccessReviewCampaignResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/access-reviews/{campaign_id}/close`
- Summary: Post Access Review Close
- Auth: SessionCookieAuth
- Parameters:
  - `campaign_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> AccessReviewTransitionRequest
- Responses: `200` `application/json` -> AccessReviewCampaignResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/access-reviews/{campaign_id}/decisions`
- Summary: Post Access Review Decisions
- Auth: SessionCookieAuth
- Parameters:
  - `campaign_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> AccessReviewDecisionBatchRequest
- Responses: `200` `application/json` -> AccessReviewCampaignResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/access-reviews/{campaign_id}/items`
- Summary: Get Access Review Items
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:access_reviews`
- Parameters:
  - `campaign_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
  - `item_type` (query, optional): Item Type
  - `principal_type` (query, optional): Principal Type
  - `decision` (query, optional): Decision
  - `apply_outcome` (query, optional): Apply Outcome
- Responses: `200` `application/json` -> AccessReviewItemListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Action Approvals

### `GET /v1/iam/action-approvals`
- Summary: Get Action Approvals
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:approvals`
- Parameters:
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
  - `action_type` (query, optional): Action Type
  - `stored_status` (query, optional): Stored Status
  - `requester_user_id` (query, optional): Requester User Id
- Responses: `200` `application/json` -> ActionApprovalListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/action-approvals`
- Summary: Post Action Approval
- Auth: SessionCookieAuth
- Parameters:
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ActionApprovalCreateRequest
- Responses: `201` `application/json` -> ActionApprovalResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/action-approvals/actions`
- Summary: Get Action Catalog
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:approvals`
- Responses: `200` `application/json` -> array[ActionDefinitionResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/action-approvals/{approval_id}`
- Summary: Get Action Approval
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:approvals`
- Parameters:
  - `approval_id` (path, required): string
- Responses: `200` `application/json` -> ActionApprovalResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/action-approvals/{approval_id}/cancel`
- Summary: Post Action Approval Cancel
- Auth: SessionCookieAuth
- Parameters:
  - `approval_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ActionApprovalCancelRequest
- Responses: `200` `application/json` -> ActionApprovalResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/action-approvals/{approval_id}/decision`
- Summary: Post Action Approval Decision
- Auth: SessionCookieAuth
- Parameters:
  - `approval_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ActionApprovalDecisionRequest
- Responses: `200` `application/json` -> ActionApprovalResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/action-approvals/{approval_id}/execute`
- Summary: Post Action Approval Execute
- Auth: SessionCookieAuth
- Parameters:
  - `approval_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ActionApprovalExecuteRequest
- Responses: `200` `application/json` -> ActionApprovalExecutionResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/action-approvals/{approval_id}/receipt`
- Summary: Get Action Receipt
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:approvals`
- Parameters:
  - `approval_id` (path, required): string
- Responses: `200` `application/json` -> ActionExecutionReceiptResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Ai

### `POST /v1/ai/daily-brief/backfill`
- Summary: Queue Daily Brief Backfill
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Request body: `application/json` -> AIDailyBriefBackfillRequest
- Responses: `200` `application/json` -> AIDailyBriefBackfillResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/ai/daily-brief/generate`
- Summary: Generate Daily Brief
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Responses: `200` `application/json` -> AIDailyBriefResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/daily-brief/latest`
- Summary: Get Latest Daily Brief
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Responses: `200` `application/json` -> AIDailyBriefResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/ai/daily-brief/queue`
- Summary: Queue Daily Brief
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Responses: `200` `application/json` -> AIQueuedTaskResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/daily-briefs`
- Summary: List Daily Briefs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `limit` (query, optional): Limit
- Responses: `200` `application/json` -> array[AIDailyBriefResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/daily-briefs/{brief_id}/sources`
- Summary: List Daily Brief Sources
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `brief_id` (path, required): string
  - `included` (query, optional): Included
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIDailyBriefSourceItemResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/ops/live`
- Summary: Get Ai Ops Live
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Responses: `200` `application/json` -> AILiveStatusResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/ops/manual-actions`
- Summary: List Ai Ops Manual Actions
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIAuditEntryResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/ops/overview`
- Summary: Get Ai Ops Overview
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `days` (query, optional): integer
- Responses: `200` `application/json` -> AIOpsOverviewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/ops/prompt-history`
- Summary: List Ai Ops Prompt History
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> array[AIAuditEntryResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
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
- Responses: `200` `application/json` -> AITaskRunListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/ops/runs/{run_id}`
- Summary: Get Ai Ops Run Detail
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Parameters:
  - `run_id` (path, required): string
- Responses: `200` `application/json` -> AITaskRunDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/ai/ops/runs/{run_id}/cancel`
- Summary: Cancel Ai Ops Run
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Parameters:
  - `run_id` (path, required): string
- Responses: `200` `application/json` -> AITaskRunResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/ai/reprocess`
- Summary: Reprocess Ai For Recent Items
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Request body: `application/json` -> AIReprocessRequest
- Responses: `200` `application/json` -> AIReprocessResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/settings`
- Summary: Get Ai Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Responses: `200` `application/json` -> AISettingsResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/ai/settings`
- Summary: Update Ai Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Request body: `application/json` -> AISettingsUpdate
- Responses: `200` `application/json` -> AISettingsResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/ai/test-connection`
- Summary: Test Ai Connection
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:ai`
- Responses: `200` `application/json` -> AITestConnectionResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/ai/usage`
- Summary: Get Ai Usage
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:ai`
- Responses: `200` `application/json` -> AIUsageSummaryResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Alerts

### `GET /v1/alerts`
- Summary: List Alert Interests
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`
- Parameters:
  - `include_disabled` (query, optional): boolean
- Responses: `200` `application/json` -> array[AlertInterestResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/alerts`
- Summary: Create Alert Interest
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`
- Request body: `application/json` -> AlertInterestCreate
- Responses: `201` `application/json` -> AlertInterestResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
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
- Responses: `200` `application/json` -> AlertMatchListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/alerts/occurrences`
- Summary: Get Alert Occurrences
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`, `read:items`
- Parameters:
  - `lifecycle_states` (query, optional): array[string]
  - `severities` (query, optional): array[string]
  - `alert_interest_id` (query, optional): Alert Interest Id
  - `suppressed` (query, optional): Suppressed
  - `snoozed` (query, optional): Snoozed
  - `since` (query, optional): Since
  - `until` (query, optional): Until
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> AlertOccurrenceListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/alerts/occurrences/bulk/acknowledge`
- Summary: Bulk Acknowledge Alert Occurrences
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`, `read:items`
- Request body: `application/json` -> AlertOccurrenceBulkUpdate
- Responses: `200` `application/json` -> AlertOccurrenceBulkResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/alerts/occurrences/bulk/close`
- Summary: Bulk Close Alert Occurrences
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`, `read:items`
- Request body: `application/json` -> AlertOccurrenceBulkUpdate
- Responses: `200` `application/json` -> AlertOccurrenceBulkResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/alerts/occurrences/evaluations`
- Summary: Get Alert Evaluations
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`
- Parameters:
  - `states` (query, optional): array[string]
  - `sources` (query, optional): array[string]
  - `item_id` (query, optional): Item Id
  - `needs_attention` (query, optional): boolean
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> AlertEvaluationRequestListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/alerts/occurrences/evaluations/{request_id}`
- Summary: Get Alert Evaluation Detail
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`
- Parameters:
  - `request_id` (path, required): string
- Responses: `200` `application/json` -> AlertEvaluationRequestResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/alerts/occurrences/evaluations/{request_id}/activity`
- Summary: Get Alert Evaluation Activity
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`
- Parameters:
  - `request_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> AlertEvaluationActivityListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/alerts/occurrences/evaluations/{request_id}/replay`
- Summary: Replay Alert Evaluation
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`
- Parameters:
  - `request_id` (path, required): string
- Request body: `application/json` -> AlertEvaluationReplayRequest
- Responses: `202` `application/json` -> AlertEvaluationReplayResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/alerts/occurrences/metrics`
- Summary: Get Alert Occurrence Metrics
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`
- Parameters:
  - `since` (query, optional): Since
  - `until` (query, optional): Until
  - `severities` (query, optional): array[string]
  - `lifecycle_states` (query, optional): array[string]
  - `suppressed` (query, optional): Suppressed
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> AlertOccurrenceMetricListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/alerts/occurrences/reconciliation/apply`
- Summary: Apply Alert Occurrence Backfill
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`, `read:items`
- Request body: `application/json` -> AlertBackfillApplyRequest
- Responses: `202` `application/json` -> AlertBackfillApplyResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/alerts/occurrences/reconciliation/preview`
- Summary: Preview Alert Occurrence Backfill
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`, `read:items`
- Request body: `application/json` -> AlertBackfillRequest
- Responses: `200` `application/json` -> AlertBackfillPreviewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/alerts/occurrences/{occurrence_id}`
- Summary: Get Alert Occurrence Detail
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`, `read:items`
- Parameters:
  - `occurrence_id` (path, required): string
- Responses: `200` `application/json` -> AlertOccurrenceResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/alerts/occurrences/{occurrence_id}/activity`
- Summary: Get Alert Occurrence Activity
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`
- Parameters:
  - `occurrence_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> AlertOccurrenceActivityListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/alerts/occurrences/{occurrence_id}/lifecycle`
- Summary: Patch Alert Occurrence Lifecycle
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`, `read:items`
- Parameters:
  - `occurrence_id` (path, required): string
- Request body: `application/json` -> AlertOccurrenceLifecycleUpdate
- Responses: `200` `application/json` -> AlertOccurrenceResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/alerts/occurrences/{occurrence_id}/snooze`
- Summary: Patch Alert Occurrence Snooze
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`, `read:items`
- Parameters:
  - `occurrence_id` (path, required): string
- Request body: `application/json` -> AlertOccurrenceSnoozeUpdate
- Responses: `200` `application/json` -> AlertOccurrenceResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/alerts/preview`
- Summary: Preview Alert Interest
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:alerts`, `read:items`
- Request body: `application/json` -> AlertInterestPreviewRequest
- Responses: `200` `application/json` -> AlertMatchListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/alerts/{alert_id}`
- Summary: Delete Alert Interest
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`
- Parameters:
  - `alert_id` (path, required): string
  - `expected_revision` (query, optional): Expected Revision
  - `expected_row_version` (query, optional): Expected Row Version
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/alerts/{alert_id}`
- Summary: Update Alert Interest
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:alerts`
- Parameters:
  - `alert_id` (path, required): string
- Request body: `application/json` -> AlertInterestUpdate
- Responses: `200` `application/json` -> AlertInterestResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Audit

### `GET /v1/audit-logs`
- Summary: List Audit Logs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:audit`
- Parameters:
  - `action` (query, optional): Action
  - `actor_user_id` (query, optional): Actor User Id
  - `actor_principal_type` (query, optional): Actor Principal Type
  - `actor_principal_id` (query, optional): Actor Principal Id
  - `credential_kind` (query, optional): Credential Kind
  - `credential_id` (query, optional): Credential Id
  - `elevation_id` (query, optional): Elevation Id
  - `approval_id` (query, optional): Approval Id
  - `execution_receipt_id` (query, optional): Execution Receipt Id
  - `resource_type` (query, optional): Resource Type
  - `resource_id` (query, optional): Resource Id
  - `request_id` (query, optional): Request Id
  - `source_ip` (query, optional): Source Ip
  - `success` (query, optional): Success
  - `created_from` (query, optional): Created From
  - `created_to` (query, optional): Created To
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> AuditLogListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/audit-logs/export`
- Summary: Export Audit Logs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:audit`
- Parameters:
  - `action` (query, optional): Action
  - `actor_user_id` (query, optional): Actor User Id
  - `actor_principal_type` (query, optional): Actor Principal Type
  - `actor_principal_id` (query, optional): Actor Principal Id
  - `credential_kind` (query, optional): Credential Kind
  - `credential_id` (query, optional): Credential Id
  - `elevation_id` (query, optional): Elevation Id
  - `approval_id` (query, optional): Approval Id
  - `execution_receipt_id` (query, optional): Execution Receipt Id
  - `resource_type` (query, optional): Resource Type
  - `resource_id` (query, optional): Resource Id
  - `request_id` (query, optional): Request Id
  - `source_ip` (query, optional): Source Ip
  - `success` (query, optional): Success
  - `created_from` (query, optional): Created From
  - `created_to` (query, optional): Created To
  - `limit` (query, optional): integer
- Responses: `200` `application/json` -> AuditLogExportResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Auth

### `POST /v1/auth/change-password`
- Summary: Change Password
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> ChangePasswordRequest
- Responses: `200` `application/json` -> ChangePasswordResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/login`
- Summary: Login
- Auth: none
- Request body: `application/json` -> LoginRequest
- Responses: `200` `application/json` -> TokenResponse, `422` `application/json` -> ApiErrorResponse
### `POST /v1/auth/logout`
- Summary: Logout
- Auth: none
- Responses: `200` `application/json` -> unspecified
### `GET /v1/auth/me`
- Summary: Me
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> CurrentUserResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/mfa/verify`
- Summary: Verify Mfa Login
- Auth: none
- Request body: `application/json` -> MFALoginVerifyRequest
- Responses: `200` `application/json` -> TokenResponse, `422` `application/json` -> ApiErrorResponse
### `DELETE /v1/auth/oidc/access-policy`
- Summary: Remove Oidc Access Policy
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `expected_revision` (query, required): integer
- Responses: `200` `application/json` -> OIDCAccessPolicyStateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/auth/oidc/access-policy`
- Summary: Get Oidc Access Policy
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Responses: `200` `application/json` -> OIDCAccessPolicyStateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/oidc/access-policy`
- Summary: Post Oidc Access Policy
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Request body: `application/json` -> OIDCAccessPolicyCreateRequest
- Responses: `201` `application/json` -> OIDCAccessPolicyStateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/auth/oidc/access-policy`
- Summary: Put Oidc Access Policy
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Request body: `application/json` -> OIDCAccessPolicyUpdateRequest
- Responses: `200` `application/json` -> OIDCAccessPolicyStateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/oidc/access-policy/mapping-sets`
- Summary: Post Oidc Mapping Set
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Request body: `application/json` -> OIDCClaimMappingSetCreateRequest
- Responses: `201` `application/json` -> OIDCAccessPolicyStateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/auth/oidc/access-policy/mapping-sets/{mapping_set_id}`
- Summary: Remove Oidc Mapping Set
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `mapping_set_id` (path, required): string
  - `expected_revision` (query, required): integer
- Responses: `200` `application/json` -> OIDCAccessPolicyStateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/auth/oidc/access-policy/mapping-sets/{mapping_set_id}`
- Summary: Put Oidc Mapping Set
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `mapping_set_id` (path, required): string
- Request body: `application/json` -> OIDCClaimMappingSetUpdateRequest
- Responses: `200` `application/json` -> OIDCAccessPolicyStateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/auth/oidc/account`
- Summary: Unlink Oidc Account
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> OIDCUnlinkRequest
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/auth/oidc/account`
- Summary: Oidc Account Status
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> OIDCAccountStatusResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/auth/oidc/callback`
- Summary: Oidc Callback
- Auth: none
- Parameters:
  - `state` (query, optional): State
  - `code` (query, optional): Code
  - `error` (query, optional): Error
- Responses: `302`, `422` `application/json` -> ApiErrorResponse
### `POST /v1/auth/oidc/link`
- Summary: Start Oidc Link
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> Payload
- Responses: `200` `application/json` -> OIDCStartResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/auth/oidc/login`
- Summary: Start Oidc Login
- Auth: none
- Responses: `200` `application/json` -> unspecified
### `GET /v1/auth/oidc/provider`
- Summary: Get Oidc Provider
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:users`
- Responses: `200` `application/json` -> OIDCProviderResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/auth/oidc/provider`
- Summary: Update Oidc Provider
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:users`
- Request body: `application/json` -> OIDCProviderUpdateRequest
- Responses: `200` `application/json` -> OIDCProviderResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/oidc/provider/test`
- Summary: Test Configured Oidc Provider
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:users`
- Responses: `200` `application/json` -> OIDCProviderTestResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/oidc/reauth`
- Summary: Start Oidc Reauthentication
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> OIDCReauthenticationStartResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/auth/oidc/settings`
- Summary: Public Oidc Settings
- Auth: none
- Responses: `200` `application/json` -> OIDCPublicSettingsResponse
### `POST /v1/auth/register`
- Summary: Register
- Auth: none
- Request body: `application/json` -> RegisterRequest
- Responses: `200` `application/json` -> UserResponse, `422` `application/json` -> ApiErrorResponse
### `GET /v1/auth/registration-settings`
- Summary: Registration Settings
- Auth: none
- Responses: `200` `application/json` -> RegistrationSettingsResponse
### `DELETE /v1/auth/security/mfa`
- Summary: Remove Totp
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> TOTPSensitiveActionRequest
- Responses: `200` `application/json` -> TOTPDisableResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/auth/security/mfa`
- Summary: Get Mfa Status
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> TOTPStatusResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/security/mfa/confirm`
- Summary: Confirm Totp
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> TOTPConfirmRequest
- Responses: `200` `application/json` -> TOTPRecoveryCodesResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/security/mfa/enroll`
- Summary: Enroll Totp
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> TOTPEnrollmentStartRequest
- Responses: `200` `application/json` -> TOTPEnrollmentStartResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/auth/security/mfa/enrollment`
- Summary: Cancel Totp Enrollment
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> TOTPEnrollmentCancelResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/security/mfa/recovery-codes`
- Summary: Replace Recovery Codes
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> TOTPSensitiveActionRequest
- Responses: `200` `application/json` -> TOTPRecoveryCodesResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/security/reauthenticate`
- Summary: Reauthenticate Local Session
- Auth: ApiTokenBearer or SessionCookieAuth
- Request body: `application/json` -> RecentAuthenticationRequest
- Responses: `200` `application/json` -> RecentAuthenticationResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/auth/security/sessions`
- Summary: List Sessions
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> AuthSessionListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/auth/security/sessions/revoke-others`
- Summary: Revoke Other Sessions
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> SessionBulkRevocationResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/auth/security/sessions/{session_id}`
- Summary: Revoke Session
- Auth: ApiTokenBearer or SessionCookieAuth
- Parameters:
  - `session_id` (path, required): string
- Responses: `200` `application/json` -> SessionRevocationResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Data Policies

### `GET /v1/iam/data-policies`
- Summary: Get Data Policy Overview
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:data_policies`
- Responses: `200` `application/json` -> DataPolicyOverviewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/iam/data-policies/feeds/{feed_id}`
- Summary: Put Feed Handling Label
- Auth: SessionCookieAuth
- Parameters:
  - `feed_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> FeedHandlingLabelAssignmentRequest
- Responses: `200` `application/json` -> FeedHandlingLabelAssignmentResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/data-policies/labels`
- Summary: Post Handling Label
- Auth: SessionCookieAuth
- Parameters:
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> HandlingLabelCreateRequest
- Responses: `201` `application/json` -> HandlingLabelMutationResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/iam/data-policies/labels/{label_id}`
- Summary: Patch Handling Label
- Auth: SessionCookieAuth
- Parameters:
  - `label_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> HandlingLabelUpdateRequest
- Responses: `200` `application/json` -> HandlingLabelMutationResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/iam/data-policies/labels/{label_id}/role-grants`
- Summary: Put Handling Label Role Grants
- Auth: SessionCookieAuth
- Parameters:
  - `label_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> HandlingLabelRoleGrantsRequest
- Responses: `200` `application/json` -> HandlingLabelMutationResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/iam/data-policies/labels/{label_id}/status`
- Summary: Put Handling Label Status
- Auth: SessionCookieAuth
- Parameters:
  - `label_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> HandlingLabelStatusRequest
- Responses: `200` `application/json` -> HandlingLabelMutationResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/iam/data-policies/mode`
- Summary: Put Data Policy Mode
- Auth: SessionCookieAuth
- Parameters:
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> DataPolicyModeUpdateRequest
- Responses: `200` `application/json` -> DataPolicyModeUpdateResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/data-policies/preflight`
- Summary: Get Data Policy Preflight
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:data_policies`
- Responses: `200` `application/json` -> DataPolicyPreflightResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Elevations

### `GET /v1/iam/elevations`
- Summary: Get Elevations
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:elevations`
- Parameters:
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
  - `target_user_id` (query, optional): Target User Id
  - `stored_status` (query, optional): Stored Status
- Responses: `200` `application/json` -> TemporaryElevationListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/elevations`
- Summary: Post Elevation
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:elevations`
- Parameters:
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ElevationRequestCreate
- Responses: `201` `application/json` -> TemporaryElevationResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/elevations/{elevation_id}`
- Summary: Get Elevation
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:elevations`
- Parameters:
  - `elevation_id` (path, required): string
- Responses: `200` `application/json` -> TemporaryElevationResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/elevations/{elevation_id}/close`
- Summary: Post Elevation Close
- Auth: ApiTokenBearer or SessionCookieAuth
- Parameters:
  - `elevation_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ElevationCloseRequest
- Responses: `200` `application/json` -> TemporaryElevationResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/elevations/{elevation_id}/decision`
- Summary: Post Elevation Decision
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `approve:elevations`
- Parameters:
  - `elevation_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ElevationDecisionRequest
- Responses: `200` `application/json` -> TemporaryElevationResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Exports

### `POST /v1/exports`
- Summary: Download Export
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Request body: `application/json` -> ArticleExportRequest
- Responses: `200`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/exports/capabilities`
- Summary: Get Export Capabilities
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Responses: `200` `application/json` -> ArticleExportCapabilitiesResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/exports/preview`
- Summary: Preview Export
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Request body: `application/json` -> ArticleExportPreviewRequest
- Responses: `200` `application/json` -> ArticleExportPreviewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Feeds

### `GET /v1/feeds`
- Summary: List Feeds
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:feeds`
- Responses: `200` `application/json` -> array[FeedResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/feeds`
- Summary: Create Feed
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Request body: `application/json` -> FeedCreate
- Responses: `201` `application/json` -> FeedResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/feeds/export`
- Summary: Export Feeds Sanitized
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:feeds`
- Responses: `200` `application/json` -> FeedExportResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/feeds/export/backup`
- Summary: Export Feeds Backup
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `admin:feeds`
- Responses: `200` `application/json` -> FeedExportResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/feeds/import`
- Summary: Import Feeds
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Request body: `application/json` -> FeedImportRequest
- Responses: `200` `application/json` -> FeedImportResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/feeds/metadata`
- Summary: Get Feed Metadata
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Request body: `application/json` -> FeedMetadataRequest
- Responses: `200` `application/json` -> FeedMetadataResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/feeds/{feed_id}`
- Summary: Delete Feed
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `admin:feeds`
- Parameters:
  - `feed_id` (path, required): string
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/feeds/{feed_id}`
- Summary: Update Feed
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Parameters:
  - `feed_id` (path, required): string
- Request body: `application/json` -> FeedUpdate
- Responses: `200` `application/json` -> FeedResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/feeds/{feed_id}/refresh`
- Summary: Refresh Feed
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:feeds`
- Parameters:
  - `feed_id` (path, required): string
- Responses: `202` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Health

### `GET /v1/health`
- Summary: Health
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/health/beat`
- Summary: Beat
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:health`
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/health/encrypted-data`
- Summary: Encrypted Data
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:health`
- Responses: `200` `application/json` -> EncryptedDataInventoryResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/health/live`
- Summary: Live
- Auth: none
- Responses: `200` `application/json` -> unspecified
### `GET /v1/health/notifications`
- Summary: Notifications
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:health`
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/health/ready`
- Summary: Ready
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/health/worker`
- Summary: Worker
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:health`
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Iam

### `GET /v1/iam/effective`
- Summary: Get My Effective Access
- Auth: ApiTokenBearer or SessionCookieAuth
- Responses: `200` `application/json` -> EffectiveAccessResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/effective/explain`
- Summary: Explain My Access
- Auth: ApiTokenBearer or SessionCookieAuth
- Parameters:
  - `permission` (query, required): string
- Responses: `200` `application/json` -> AccessExplanationResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/groups`
- Summary: Get Groups
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Responses: `200` `application/json` -> array[GroupResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/groups`
- Summary: Post Group
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Request body: `application/json` -> GroupWriteRequest
- Responses: `201` `application/json` -> GroupResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/iam/groups/{group_id}`
- Summary: Remove Group
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `group_id` (path, required): string
  - `expected_revision` (query, required): integer
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/iam/groups/{group_id}`
- Summary: Patch Group
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `group_id` (path, required): string
- Request body: `application/json` -> GroupUpdateRequest
- Responses: `200` `application/json` -> GroupResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/groups/{group_id}/members`
- Summary: Get Group Members
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Parameters:
  - `group_id` (path, required): string
  - `limit` (query, optional): integer
  - `offset` (query, optional): integer
- Responses: `200` `application/json` -> array[GroupMemberResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/groups/{group_id}/members`
- Summary: Post Group Member
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `group_id` (path, required): string
- Request body: `application/json` -> GroupMemberRequest
- Responses: `201` `application/json` -> GroupMemberResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/iam/groups/{group_id}/members/{membership_id}`
- Summary: Delete Group Member
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `group_id` (path, required): string
  - `membership_id` (path, required): string
  - `expected_group_revision` (query, required): integer
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/groups/{group_id}/role-assignments`
- Summary: Get Group Roles
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Parameters:
  - `group_id` (path, required): string
- Responses: `200` `application/json` -> array[GroupRoleAssignmentResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/groups/{group_id}/role-assignments`
- Summary: Post Group Role
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `group_id` (path, required): string
- Request body: `application/json` -> GroupRoleRequest
- Responses: `201` `application/json` -> GroupResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/iam/groups/{group_id}/role-assignments/{assignment_id}`
- Summary: Delete Group Role
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `group_id` (path, required): string
  - `assignment_id` (path, required): string
  - `expected_group_revision` (query, required): integer
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/permissions`
- Summary: Get Permissions
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Responses: `200` `application/json` -> array[PermissionResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/roles`
- Summary: Get Roles
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Responses: `200` `application/json` -> array[RoleResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/roles`
- Summary: Post Role
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Request body: `application/json` -> RoleWriteRequest
- Responses: `201` `application/json` -> RoleResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/iam/roles/{role_id}`
- Summary: Remove Role
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `role_id` (path, required): string
  - `expected_revision` (query, required): integer
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/roles/{role_id}`
- Summary: Get Role
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Parameters:
  - `role_id` (path, required): string
- Responses: `200` `application/json` -> RoleResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/iam/roles/{role_id}`
- Summary: Patch Role
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `role_id` (path, required): string
- Request body: `application/json` -> RoleUpdateRequest
- Responses: `200` `application/json` -> RoleResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/users/{user_id}/effective`
- Summary: Get User Effective Access
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Parameters:
  - `user_id` (path, required): string
- Responses: `200` `application/json` -> EffectiveAccessResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/users/{user_id}/role-assignments`
- Summary: Get User Roles
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:iam`
- Parameters:
  - `user_id` (path, required): string
- Responses: `200` `application/json` -> array[UserRoleAssignmentResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/users/{user_id}/role-assignments`
- Summary: Post User Role
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `user_id` (path, required): string
- Request body: `application/json` -> UserRoleAssignmentRequest
- Responses: `201` `application/json` -> UserRoleAssignmentResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/iam/users/{user_id}/role-assignments/{assignment_id}`
- Summary: Delete User Role
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:iam`
- Parameters:
  - `user_id` (path, required): string
  - `assignment_id` (path, required): string
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Integrations

### `GET /v1/integrations`
- Summary: List Integrations
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:integrations`
- Responses: `200` `application/json` -> array[IntegrationSummaryResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/integrations/connectors`
- Summary: Get Integration Connectors
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:integrations`
- Responses: `200` `application/json` -> array[IntegrationConnectorResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/integrations/deliveries/{delivery_id}/replay`
- Summary: Replay Integration Delivery
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:integrations`
- Parameters:
  - `delivery_id` (path, required): string
- Responses: `200` `application/json` -> IntegrationDeliveryReplayResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/integrations/smtp/analytics`
- Summary: Get Smtp Analytics
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:integrations`
- Responses: `200` `application/json` -> SMTPAnalyticsResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/integrations/smtp/hooks`
- Summary: Get Smtp Hooks
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:integrations`
- Responses: `200` `application/json` -> array[SMTPHookResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/integrations/smtp/hooks`
- Summary: Create Smtp Hook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:integrations`
- Request body: `application/json` -> SMTPHookWrite
- Responses: `201` `application/json` -> SMTPHookResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/integrations/smtp/hooks/test`
- Summary: Test Smtp Hook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:integrations`
- Request body: `application/json` -> SMTPHookTestRequest
- Responses: `200` `application/json` -> SMTPTestResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/integrations/smtp/hooks/{hook_id}`
- Summary: Delete Smtp Hook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:integrations`
- Parameters:
  - `hook_id` (path, required): string
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/integrations/smtp/hooks/{hook_id}`
- Summary: Update Smtp Hook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:integrations`
- Parameters:
  - `hook_id` (path, required): string
- Request body: `application/json` -> SMTPHookWrite
- Responses: `200` `application/json` -> SMTPHookResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/integrations/smtp/hooks/{hook_id}/deliveries`
- Summary: Get Smtp Hook Deliveries
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:integrations`
- Parameters:
  - `hook_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> SMTPDeliveryListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/integrations/smtp/hooks/{hook_id}/deliveries/{delivery_id}/replay`
- Summary: Replay Smtp Hook Delivery
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:integrations`
- Parameters:
  - `hook_id` (path, required): string
  - `delivery_id` (path, required): string
- Responses: `200` `application/json` -> IntegrationDeliveryReplayResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/integrations/smtp/hooks/{hook_id}/test-runs`
- Summary: Get Smtp Hook Test Runs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:integrations`
- Parameters:
  - `hook_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> SMTPTestRunListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/integrations/smtp/settings`
- Summary: Get Smtp Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:integrations`
- Responses: `200` `application/json` -> SMTPSettingsResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/integrations/smtp/settings`
- Summary: Update Smtp Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:integrations`
- Request body: `application/json` -> SMTPSettingsUpdate
- Responses: `200` `application/json` -> SMTPSettingsResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/integrations/smtp/template-defaults`
- Summary: Get Smtp Template Defaults
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:integrations`
- Responses: `200` `application/json` -> array[SMTPTemplateDefaultResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/integrations/smtp/test`
- Summary: Test Smtp Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:integrations`
- Request body: `application/json` -> SMTPTestRequest
- Responses: `200` `application/json` -> SMTPTestResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Investigations

### `GET /v1/investigations`
- Summary: Get Investigations
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:investigations`
- Parameters:
  - `q` (query, optional): Q
  - `statuses` (query, optional): array[string]
  - `severities` (query, optional): array[string]
  - `assigned_to_me` (query, optional): boolean
  - `include_archived` (query, optional): boolean
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> InvestigationListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/investigations`
- Summary: Post Investigation
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Request body: `application/json` -> InvestigationCreate
- Responses: `201` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/investigations/member-candidates`
- Summary: Get Investigation Member Candidates
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `q` (query, optional): Q
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> InvestigationMemberCandidateListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/investigations/{investigation_id}`
- Summary: Get Investigation
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:investigations`
- Parameters:
  - `investigation_id` (path, required): string
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/investigations/{investigation_id}`
- Summary: Patch Investigation
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
- Request body: `application/json` -> InvestigationUpdate
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/investigations/{investigation_id}/activity`
- Summary: Get Investigation Activity
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:investigations`
- Parameters:
  - `investigation_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> InvestigationActivityListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/investigations/{investigation_id}/evidence`
- Summary: Get Investigation Evidence
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:investigations`
- Parameters:
  - `investigation_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> InvestigationEvidenceListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/investigations/{investigation_id}/evidence`
- Summary: Post Investigation Evidence
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
- Request body: `application/json` -> InvestigationEvidenceAdd
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/investigations/{investigation_id}/evidence/{evidence_id}`
- Summary: Delete Investigation Evidence
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
  - `evidence_id` (path, required): string
  - `expected_version` (query, required): integer
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/investigations/{investigation_id}/members`
- Summary: Post Investigation Member
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
- Request body: `application/json` -> InvestigationMemberAdd
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/investigations/{investigation_id}/members/{member_user_id}`
- Summary: Delete Investigation Member
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
  - `member_user_id` (path, required): string
  - `expected_version` (query, required): integer
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/investigations/{investigation_id}/members/{member_user_id}`
- Summary: Patch Investigation Member
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
  - `member_user_id` (path, required): string
- Request body: `application/json` -> InvestigationMemberUpdate
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/investigations/{investigation_id}/notes`
- Summary: Get Investigation Notes
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:investigations`
- Parameters:
  - `investigation_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> InvestigationNoteListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/investigations/{investigation_id}/notes`
- Summary: Post Investigation Note
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
- Request body: `application/json` -> InvestigationNoteCreate
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/investigations/{investigation_id}/notes/{note_id}`
- Summary: Delete Investigation Note
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
  - `note_id` (path, required): string
  - `expected_note_version` (query, required): integer
  - `expected_investigation_version` (query, required): integer
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/investigations/{investigation_id}/notes/{note_id}`
- Summary: Patch Investigation Note
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:investigations`
- Parameters:
  - `investigation_id` (path, required): string
  - `note_id` (path, required): string
- Request body: `application/json` -> InvestigationNoteUpdate
- Responses: `200` `application/json` -> InvestigationDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

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
  - `ai_relevance` (query, optional): string
  - `is_starred` (query, optional): Is Starred
  - `is_read` (query, optional): Is Read
  - `since` (query, optional): Since
  - `until` (query, optional): Until
  - `has_article` (query, optional): Has Article
  - `date_basis` (query, optional): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
  - `sort` (query, optional): string
- Responses: `200` `application/json` -> ItemListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/items/{item_id}`
- Summary: Get Item
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `item_id` (path, required): string
- Responses: `200` `application/json` -> ItemDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/items/{item_id}/article-preview`
- Summary: Get Item Article Preview
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `item_id` (path, required): string
- Responses: `200` `text/html` -> string, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
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
- Responses: `200` `application/json` -> ItemGraphResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/items/{item_id}/note`
- Summary: Set Item Note
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> NoteUpdateRequest
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/items/{item_id}/read`
- Summary: Set Item Read
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> ReadUpdateRequest
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/items/{item_id}/retry-article-fetch`
- Summary: Retry Item Article Fetch
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Responses: `202` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/items/{item_id}/star`
- Summary: Set Item Star
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> StarUpdateRequest
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/items/{item_id}/tag-suggestions`
- Summary: Get Item Tag Suggestions
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:items`
- Parameters:
  - `item_id` (path, required): string
- Responses: `200` `application/json` -> ItemTagSuggestionListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/items/{item_id}/tags`
- Summary: Set Item Tags
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:items`
- Parameters:
  - `item_id` (path, required): string
- Request body: `application/json` -> ItemTagsUpdateRequest
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Notifications

### `GET /v1/notifications/analytics`
- Summary: Get Notifications Analytics
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Responses: `200` `application/json` -> NotificationAnalyticsResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/notifications/template-variables`
- Summary: Get Notification Template Variables
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Responses: `200` `application/json` -> array[NotificationTemplateVariable], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/notifications/webhooks`
- Summary: List Notification Webhooks
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Responses: `200` `application/json` -> array[NotificationWebhookResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/notifications/webhooks`
- Summary: Create Notification Webhook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Request body: `application/json` -> NotificationWebhookWrite
- Responses: `201` `application/json` -> NotificationWebhookResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/notifications/webhooks/test`
- Summary: Test Notification Webhook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Request body: `application/json` -> NotificationWebhookTestRequest
- Responses: `200` `application/json` -> NotificationWebhookTestResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/notifications/webhooks/{webhook_id}`
- Summary: Delete Notification Webhook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Parameters:
  - `webhook_id` (path, required): string
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/notifications/webhooks/{webhook_id}`
- Summary: Update Notification Webhook
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Parameters:
  - `webhook_id` (path, required): string
- Request body: `application/json` -> NotificationWebhookWrite
- Responses: `200` `application/json` -> NotificationWebhookResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/notifications/webhooks/{webhook_id}/deliveries`
- Summary: List Notification Webhook Deliveries
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:notifications`
- Parameters:
  - `webhook_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> NotificationWebhookDeliveryListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/notifications/webhooks/{webhook_id}/deliveries/{delivery_id}/retry`
- Summary: Retry Notification Webhook Delivery
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:notifications`
- Parameters:
  - `webhook_id` (path, required): string
  - `delivery_id` (path, required): string
- Responses: `200` `application/json` -> NotificationWebhookDeliveryResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Operations

### `GET /v1/operations/diagnostics`
- Summary: Diagnostics
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:operations`
- Responses: `200` `application/json` -> OperationsDiagnosticsResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/operations/overview`
- Summary: Overview
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:operations`
- Responses: `200` `application/json` -> OperationsOverviewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/operations/runs`
- Summary: Runs
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:operations`
- Parameters:
  - `operation_type` (query, optional): Operation Type
  - `status` (query, optional): Status
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> SystemOperationRunListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Reports

### `GET /v1/reports`
- Summary: List Reports
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:reports`
- Parameters:
  - `status` (query, optional): Status
  - `limit` (query, optional): integer
  - `offset` (query, optional): integer
- Responses: `200` `application/json` -> array[ReportListItem], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/reports`
- Summary: Create Report
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `Idempotency-Key` (header, optional): Idempotency-Key
- Request body: `application/json` -> ReportCreateRequest
- Responses: `202` `application/json` -> ReportQueueResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/reports/capabilities`
- Summary: Get Report Capabilities
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:reports`
- Responses: `200` `application/json` -> ReportCapabilitiesResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/reports/preview`
- Summary: Preview Report
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Request body: `application/json` -> ReportPreviewRequest
- Responses: `200` `application/json` -> ReportPreviewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/reports/schedules`
- Summary: List Schedules
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:reports`
- Responses: `200` `application/json` -> array[ReportScheduleResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/reports/schedules`
- Summary: Create Schedule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `Idempotency-Key` (header, optional): Idempotency-Key
- Request body: `application/json` -> ReportScheduleCreate
- Responses: `201` `application/json` -> ReportScheduleResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/reports/schedules/{schedule_id}`
- Summary: Remove Schedule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `schedule_id` (path, required): string
  - `If-Match` (header, optional): If-Match
- Responses: `204`, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `412` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/reports/schedules/{schedule_id}`
- Summary: Update Schedule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `schedule_id` (path, required): string
  - `If-Match` (header, optional): If-Match
- Request body: `application/json` -> ReportScheduleUpdate
- Responses: `200` `application/json` -> ReportScheduleResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `412` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/reports/schedules/{schedule_id}/run`
- Summary: Run Schedule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `schedule_id` (path, required): string
  - `Idempotency-Key` (header, optional): Idempotency-Key
  - `If-Match` (header, optional): If-Match
- Responses: `202` `application/json` -> array[ReportQueueResponse], `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `412` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/reports/templates`
- Summary: List Report Templates
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:reports`
- Responses: `200` `application/json` -> array[ReportTemplateResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/reports/templates`
- Summary: Create Template
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `Idempotency-Key` (header, optional): Idempotency-Key
- Request body: `application/json` -> ReportTemplateCreate
- Responses: `201` `application/json` -> ReportTemplateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/reports/templates/{template_id}`
- Summary: Remove Template
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `template_id` (path, required): string
  - `If-Match` (header, optional): If-Match
- Responses: `204`, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `412` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/reports/templates/{template_id}`
- Summary: Update Template
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `template_id` (path, required): string
  - `If-Match` (header, optional): If-Match
- Request body: `application/json` -> ReportTemplateUpdate
- Responses: `200` `application/json` -> ReportTemplateResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `412` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/reports/templates/{template_id}/clone`
- Summary: Clone Template
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:reports`
- Parameters:
  - `template_id` (path, required): string
  - `Idempotency-Key` (header, optional): Idempotency-Key
- Responses: `201` `application/json` -> ReportTemplateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/reports/{report_id}`
- Summary: Remove Report
- Auth: ApiTokenBearer or SessionCookieAuth
- Parameters:
  - `report_id` (path, required): string
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/reports/{report_id}`
- Summary: Get Report
- Auth: ApiTokenBearer or SessionCookieAuth
- Parameters:
  - `report_id` (path, required): string
- Responses: `200` `application/json` -> ReportDetailResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/reports/{report_id}/download`
- Summary: Download Report
- Auth: ApiTokenBearer or SessionCookieAuth
- Parameters:
  - `report_id` (path, required): string
  - `format` (query, optional): string
- Responses: `200` `application/json` -> unspecified, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/reports/{report_id}/retry`
- Summary: Retry Report
- Auth: ApiTokenBearer or SessionCookieAuth
- Parameters:
  - `report_id` (path, required): string
  - `Idempotency-Key` (header, optional): Idempotency-Key
- Responses: `202` `application/json` -> ReportQueueResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Service-Accounts

### `GET /v1/iam/service-accounts`
- Summary: Get Service Accounts
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:service_accounts`
- Parameters:
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> ServiceAccountListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/service-accounts`
- Summary: Post Service Account
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Request body: `application/json` -> ServiceAccountCreateRequest
- Responses: `201` `application/json` -> ServiceAccountResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/iam/service-accounts/{service_account_id}`
- Summary: Delete Disabled Service Account
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
  - `expected_revision` (query, required): integer
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/service-accounts/{service_account_id}`
- Summary: Get Service Account
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
- Responses: `200` `application/json` -> ServiceAccountResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/iam/service-accounts/{service_account_id}`
- Summary: Patch Service Account
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
- Request body: `application/json` -> ServiceAccountUpdateRequest
- Responses: `200` `application/json` -> ServiceAccountResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/service-accounts/{service_account_id}/credentials`
- Summary: Get Credentials
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> ServiceAccountCredentialListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/service-accounts/{service_account_id}/credentials`
- Summary: Post Credential
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ServiceAccountCredentialIssueRequest
- Responses: `201` `application/json` -> ServiceAccountCredentialIssueResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/service-accounts/{service_account_id}/credentials/{credential_id}/revoke`
- Summary: Post Revoke Credential
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
  - `credential_id` (path, required): string
- Request body: `application/json` -> ServiceAccountRevisionRequest
- Responses: `200` `application/json` -> ServiceAccountCredentialResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/service-accounts/{service_account_id}/credentials/{credential_id}/rotate`
- Summary: Post Rotate Credential
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
  - `credential_id` (path, required): string
  - `Idempotency-Key` (header, required): string
- Request body: `application/json` -> ServiceAccountCredentialIssueRequest
- Responses: `201` `application/json` -> ServiceAccountCredentialRotateResponse, `400` `application/json` -> ApiErrorResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/service-accounts/{service_account_id}/disable`
- Summary: Post Disable Service Account
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
- Request body: `application/json` -> ServiceAccountRevisionRequest
- Responses: `200` `application/json` -> ServiceAccountResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/iam/service-accounts/{service_account_id}/role-assignments`
- Summary: Get Role Assignments
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
- Responses: `200` `application/json` -> array[ServiceAccountRoleAssignmentResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/iam/service-accounts/{service_account_id}/role-assignments`
- Summary: Post Role Assignment
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
- Request body: `application/json` -> ServiceAccountRoleAssignmentRequest
- Responses: `201` `application/json` -> ServiceAccountRoleAssignmentResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/iam/service-accounts/{service_account_id}/role-assignments/{assignment_id}`
- Summary: Delete Role Assignment
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:service_accounts`
- Parameters:
  - `service_account_id` (path, required): string
  - `assignment_id` (path, required): string
  - `expected_revision` (query, required): integer
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `404` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Stats

### `GET /v1/stats/activity-heatmap`
- Summary: Get Activity Heatmap
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:stats`
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> ActivityHeatmapResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/stats/feed-timeseries`
- Summary: Get Feed Timeseries
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:stats`
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
  - `top_feeds` (query, optional): Top Feeds
- Responses: `200` `application/json` -> FeedTimeSeriesResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/stats/overview`
- Summary: Get Stats Overview
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:stats`
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> StatsOverviewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/stats/signal-radar`
- Summary: Get Signal Radar
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:stats`
- Parameters:
  - `days` (query, optional): integer
  - `feed_ids` (query, optional): Feed Ids
- Responses: `200` `application/json` -> SignalRadarResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Tagging

### `POST /v1/tagging/reapply`
- Summary: Queue Tagging Reapply
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tagging`
- Request body: `application/json` -> TaggingReapplyRequest
- Responses: `200` `application/json` -> TaggingReapplyResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/tagging/rules`
- Summary: Create Tagging Rule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tagging`
- Request body: `application/json` -> TaggingRuleWrite
- Responses: `201` `application/json` -> TaggingRuleResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/tagging/rules/preview`
- Summary: Preview Tagging Rule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tagging`
- Request body: `application/json` -> TaggingRulePreviewRequest
- Responses: `200` `application/json` -> TaggingRulePreviewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/tagging/rules/{rule_id}`
- Summary: Delete Tagging Rule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tagging`
- Parameters:
  - `rule_id` (path, required): string
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/tagging/rules/{rule_id}`
- Summary: Update Tagging Rule
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tagging`
- Parameters:
  - `rule_id` (path, required): string
- Request body: `application/json` -> TaggingRuleWrite
- Responses: `200` `application/json` -> TaggingRuleResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/tagging/settings`
- Summary: Get Tagging Settings Bundle
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tagging`
- Responses: `200` `application/json` -> TaggingSettingsBundleResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/tagging/settings`
- Summary: Update Tagging Settings
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tagging`
- Request body: `application/json` -> TaggingSettingsUpdate
- Responses: `200` `application/json` -> TaggingSettingsResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Tags

### `GET /v1/tags`
- Summary: List Tags
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tags`
- Responses: `200` `application/json` -> array[TagResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/tags`
- Summary: Create Tag
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tags`
- Request body: `application/json` -> TagCreate
- Responses: `201` `application/json` -> TagResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Tokens

### `GET /v1/tokens`
- Summary: List Tokens
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tokens`
- Parameters:
  - `user_id` (query, optional): User Id
- Responses: `200` `application/json` -> array[ApiTokenResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/tokens`
- Summary: Create Token
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tokens`
- Request body: `application/json` -> ApiTokenCreateRequest
- Responses: `201` `application/json` -> ApiTokenCreateResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/tokens/inventory`
- Summary: List Token Inventory
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:tokens`
- Parameters:
  - `user_id` (query, optional): User Id
  - `page` (query, optional): integer
  - `page_size` (query, optional): integer
- Responses: `200` `application/json` -> ApiTokenListResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/tokens/{token_id}`
- Summary: Revoke Token
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:tokens`
- Parameters:
  - `token_id` (path, required): string
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Users

### `GET /v1/users`
- Summary: List Users
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:users`
- Responses: `200` `application/json` -> array[UserAdminResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/users`
- Summary: Create User
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:users`
- Request body: `application/json` -> UserCreateRequest
- Responses: `201` `application/json` -> UserAdminResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/users/directory`
- Summary: List User Directory
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:users`
- Parameters:
  - `q` (query, optional): Q
  - `role` (query, optional): Role
  - `provisioning_source` (query, optional): Provisioning Source
  - `limit` (query, optional): integer
  - `offset` (query, optional): integer
- Responses: `200` `application/json` -> UserDirectoryResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/users/{user_id}`
- Summary: Get User
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:users`
- Parameters:
  - `user_id` (path, required): string
- Responses: `200` `application/json` -> UserAdminResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/users/{user_id}`
- Summary: Update User
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:users`
- Parameters:
  - `user_id` (path, required): string
- Request body: `application/json` -> UserUpdateRequest
- Responses: `200` `application/json` -> UserAdminResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `409` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/users/{user_id}/mfa/reset`
- Summary: Reset User Mfa
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:users`
- Parameters:
  - `user_id` (path, required): string
- Request body: `application/json` -> AdminMFAResetRequest
- Responses: `200` `application/json` -> AdminMFAResetResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Views

### `GET /v1/views`
- Summary: List Views
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:views`
- Responses: `200` `application/json` -> array[SavedViewResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/views`
- Summary: Create View
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:views`
- Request body: `application/json` -> SavedViewCreate
- Responses: `201` `application/json` -> SavedViewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `DELETE /v1/views/{view_id}`
- Summary: Delete View
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:views`
- Parameters:
  - `view_id` (path, required): string
- Responses: `204`, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PATCH /v1/views/{view_id}`
- Summary: Update View
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:views`
- Parameters:
  - `view_id` (path, required): string
- Request body: `application/json` -> SavedViewUpdate
- Responses: `200` `application/json` -> SavedViewResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

## Workspace

### `GET /v1/workspace/effective`
- Summary: Get My Effective Workspace
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:workspace`
- Responses: `200` `application/json` -> WorkspaceEffectiveResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/workspace/modules`
- Summary: Get Workspace Modules
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:workspace`
- Responses: `200` `application/json` -> WorkspaceRegistryResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/workspace/preferences`
- Summary: Get My Workspace Preferences
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:workspace`
- Responses: `200` `application/json` -> WorkspaceUserPreferenceResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/workspace/preferences`
- Summary: Put My Workspace Preferences
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:workspace_preferences`
- Request body: `application/json` -> WorkspaceUserPreferenceWriteRequest
- Responses: `200` `application/json` -> WorkspaceUserPreferenceResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/workspace/preferences/reset`
- Summary: Reset My Workspace Preferences
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:workspace_preferences`
- Request body: `application/json` -> WorkspaceUserPreferenceResetRequest
- Responses: `200` `application/json` -> WorkspaceUserPreferenceResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/workspace/role-policies`
- Summary: Get Workspace Role Policies
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:workspace`
- Responses: `200` `application/json` -> array[WorkspaceRolePolicyResponse], `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `GET /v1/workspace/role-policies/{role}`
- Summary: Get Workspace Role Policy
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `read:workspace`
- Parameters:
  - `role` (path, required): string
- Responses: `200` `application/json` -> WorkspaceRolePolicyResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `PUT /v1/workspace/role-policies/{role}`
- Summary: Put Workspace Role Policy
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:workspace`
- Parameters:
  - `role` (path, required): string
- Request body: `application/json` -> WorkspaceRolePolicyWriteRequest
- Responses: `200` `application/json` -> WorkspaceRolePolicyResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse
### `POST /v1/workspace/role-policies/{role}/reset`
- Summary: Reset Workspace Role Policy
- Auth: ApiTokenBearer or SessionCookieAuth
- Token scopes: `write:workspace`
- Parameters:
  - `role` (path, required): string
- Request body: `application/json` -> WorkspaceRolePolicyResetRequest
- Responses: `200` `application/json` -> WorkspaceRolePolicyResponse, `401` `application/json` -> ApiErrorResponse, `403` `application/json` -> ApiErrorResponse, `422` `application/json` -> ApiErrorResponse, `503` `application/json` -> ApiErrorResponse

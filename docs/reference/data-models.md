# Data Models and Contracts

## Backend Database Models (`backend/app/models`)

### `User`

- `id: UUID` (PK)
- `email: string` (unique, indexed)
- `password_hash: string`
- `role: string` (`admin|analyst|viewer`, default `viewer`)
- `is_active: bool` (default `true`)
- `created_at: timestamptz`

### `ApiToken`

- `id: UUID` (PK)
- `user_id: UUID` (FK users)
- `name: string(255)`
- `token_prefix: string(32)` (unique index)
- `token_hash: string(64)` (unique)
- `scopes: JSON string[]`
- `last_used_at: timestamptz?`
- `expires_at: timestamptz?`
- `revoked_at: timestamptz?`
- `created_at: timestamptz`

### `Feed`

- `id: UUID` (PK)
- `name: string`
- `url: text` (unique)
- `description: text?`
- `site_url: text?`
- `language: string(64)?`
- `enabled: bool`
- `fetch_mode: string(16)` (`interval|schedule`)
- `fetch_interval_seconds: int`
- `schedule_cron: text?`
- `etag: text?`
- `last_modified: text?`
- `last_fetch_at: timestamptz?`
- `last_success_at: timestamptz?`
- `error_count: int`
- `last_error: text?`
- `created_at: timestamptz`

### `Item`

- `id: UUID` (PK)
- `feed_id: UUID` (FK feeds)
- `source_guid: text?`
- `url: text`
- `canonical_url: text?`
- `title: text`
- `summary: text?`
- `published_at: timestamptz?`
- `first_seen_at: timestamptz`
- `dedupe_key: text` (unique)
- `content_hash: string(64)`
- `status: string(32)` (default `new`)
- `last_error: text?`
- `updated_at: timestamptz`

Indexes include feed/source/canonical/published/first_seen/content hash and partial unique `(feed_id, source_guid)` when GUID exists.

### `Article`

- `id: UUID` (PK)
- `item_id: UUID` (FK items, unique)
- `final_url: text`
- `retrieved_at: timestamptz`
- `http_status: int`
- `content_type: text?`
- `title_extracted: text?`
- `text: text?`
- `extraction_method: text?`
- `language: text?`
- `word_count: int?`
- `fetch_ms: int?`
- `error: text?`

### `ItemState`

Composite PK `(user_id, item_id)`:

- `user_id: UUID` (FK users)
- `item_id: UUID` (FK items)
- `is_read: bool`
- `is_starred: bool`
- `note: text?`
- `updated_at: timestamptz`

### `Tag` and `ItemTag`

`Tag`:

- `id: UUID` (PK)
- `name: string` (unique)

`ItemTag` (join table, composite PK):

- `item_id: UUID` (FK items)
- `tag_id: UUID` (FK tags)
- `confidence: float`
- `source: string(16)` (`rule|ioc|manual|ml`)
- `rules_version: string(64)?`
- `updated_at: timestamptz`

### `TagFeedbackEvent`

- `id: UUID` (PK)
- `item_id: UUID` (FK items)
- `user_id: UUID` (FK users)
- `tag_name: string(64)`
- `signal_type: string(24)` (`manual_add|manual_remove|star|unstar|read|unread`)
- `signal_value: float`
- `created_at: timestamptz`

### `SavedView`

- `id: UUID` (PK)
- `user_id: UUID` (FK users)
- `name: string`
- `query_json: JSON object`
- `created_at: timestamptz`

### `AuditLog`

- `id: UUID` (PK)
- `actor_user_id: UUID?` (FK users, `SET NULL` on delete)
- `action: text`
- `resource_type: text`
- `resource_id: text?`
- `success: bool`
- `metadata_json: JSON object`
- `created_at: timestamptz`

### `ItemClassification`

Primary key on `item_id`:

- `item_id: UUID` (FK items)
- `primary_category: string(64)`
- `secondary_categories: JSON string[]`
- `confidence: float`
- `scores_json: JSON {category: score}`
- `matched_terms_json: JSON {category: terms[]}`
- `source_hash: string(64)`
- `rules_version: string(32)` (default `v1` in DB model)
- `classified_at: timestamptz`

### `IOC` and `ItemIOC`

`IOC`:

- `id: UUID` (PK)
- `type: string(32)`
- `value_raw: text`
- `value_norm: text`
- `first_seen_at: timestamptz`
- `last_seen_at: timestamptz`
- Unique constraint: `(type, value_norm)`

`ItemIOC` (join table, composite PK):

- `item_id: UUID`
- `ioc_id: UUID`
- `source_section: string(32)`
- `occurrences: int`
- `confidence: float`

### `AlertInterest`

- `id: UUID` (PK)
- `user_id: UUID` (FK users)
- `name: string(255)`
- `category: string(64)`
- `keywords: JSON string[]`
- `enabled: bool`
- `created_at: timestamptz`
- `updated_at: timestamptz`

### `NotificationWebhook`

- `id: UUID` (PK)
- `user_id: UUID` (FK users)
- `name: string(255)`
- `enabled: bool`
- `event_type: string(64)` (`rss_item_new|alert_match|feed_failing|webhook_failed|daily_digest`)
- `url_template: text`
- `method: string(16)`
- `feed_scope: string(16)` (`all|selected`)
- `feed_ids_json: JSON string[]`
- `query_params_json: JSON [{key,value}]`
- `headers_json: JSON [{key,value}]`
- `body_mode: string(16)` (`none|json|form|raw`)
- `body_fields_json: JSON [{key,value}]`
- `body_template: text?`
- `timeout_seconds: int`
- `created_at: timestamptz`
- `updated_at: timestamptz`

### `NotificationWebhookDelivery`

- `id: UUID` (PK)
- `webhook_id: UUID` (FK notification_webhooks)
- `user_id: UUID` (FK users)
- `event_type_snapshot: string(64)`
- `item_id: UUID?` (FK items, `SET NULL`)
- `feed_id: UUID?` (FK feeds, `SET NULL`)
- `delivery_kind: string(16)` (`live|retry`)
- `success: bool`
- `status_code: int?`
- `duration_ms: int?`
- `timeout_seconds: int`
- `rendered_url: text`
- `rendered_method: string(16)`
- `rendered_headers_json: JSON [{key,value}]`
- `rendered_query_params_json: JSON [{key,value}]`
- `rendered_body: text?`
- `response_body_preview: text?`
- `error: text?`
- `item_title_snapshot: text?`
- `feed_name_snapshot: string(255)?`
- `attempted_at: timestamptz`

### `AISettings`

- `id: UUID` (PK)
- `provider_type: string(32)` (`openai_compatible`)
- `base_url: text?`
- `model: string(255)?`
- `temperature: float`
- `max_completion_tokens: int`
- `request_timeout_seconds: int`
- `request_max_retries: int`
- `summary_enabled: bool`
- `relevance_enabled: bool`
- `daily_brief_enabled: bool`
- `auto_enrich_new_items: bool`
- `daily_brief_window_hours: int`
- `daily_brief_max_items: int`
- `daily_brief_history_limit: int`
- `daily_brief_schedule_hour_utc: int`
- `daily_brief_schedule_minute_utc: int`
- company profile fields and prompt template/instruction fields

### `AIDailyBrief`

- `id: UUID` (PK)
- `brief_date: date`
- `status: string(32)`
- `window_start: timestamptz`
- `window_end: timestamptz`
- `title: text?`
- `brief_text: text?`
- `key_points_json: JSON string[]`
- `recommended_actions_json: JSON string[]`
- `top_item_ids_json: JSON string[]`
- provider/model/token/latency accounting fields
- `generated_at: timestamptz?`
- `error: text?`

### `AITaskRun`

- `id: UUID` (PK)
- `task_type: string(32)` (`item_enrichment|daily_brief|connection_test|reprocess`)
- `trigger_source: string(16)` (`auto|manual|scheduled`)
- `status: string(16)` (`queued|running|ready|error|skipped`)
- `reason: string(64)?`
- `celery_task_id: string(255)?`
- `worker_name: string(255)?`
- `actor_user_id: UUID?`
- `item_id: UUID?`
- `daily_brief_id: UUID?`
- `parent_run_id: UUID?`
- progress counters, token accounting, prompt/response sizing, metadata, timestamps

### `TaggingSettings`

- `id: UUID` (PK)
- `enabled_categories_json: JSON string[]`
- `min_auto_tag_confidence: float`
- `secondary_tag_limit: int`
- `created_at: timestamptz`
- `updated_at: timestamptz`

### `TaggingRule`

- `id: UUID` (PK)
- `name: string(255)`
- `tag_name: string(64)`
- `enabled: bool`
- `match_type: string(16)` (`contains|regex`)
- `pattern: text`
- `case_sensitive: bool`
- `applies_to_json: JSON string[]`
- `required_categories_json: JSON string[]`
- `feed_scope: string(16)` (`all|selected`)
- `feed_ids_json: JSON string[]`
- `min_classification_confidence: float?`
- `created_at: timestamptz`
- `updated_at: timestamptz`

## API Schemas (`backend/app/schemas`)

### Auth Schemas

- `LoginRequest`: `email`, `password`
- `RegisterRequest`: `email`, `password(8..256)`
- `ChangePasswordRequest`: `current_password`, `new_password(8..256)`
- `TokenResponse`: `token_type`, `csrf_token`
- `RegistrationSettingsResponse`: `allow_self_registration`, `ai_enabled`
- `UserResponse`: `id`, `email`, `role`, `is_active`, `is_approved`, `approved_at`, `created_at`

### Feed Schemas

- `FeedCreate`, `FeedUpdate` with mode-dependent validation for `fetch_interval_seconds` and `schedule_cron`
- `FeedMetadataRequest/Response`
- `FeedImportEntry`, `FeedImportRequest`, `FeedImportResponse`
- `FeedExportResponse`
- `FeedResponse`

### Item Schemas

- `ItemListEntry`, `ItemListResponse`
- `ArticleResponse`
- `ItemStateResponse`
- `ItemClassificationResponse`
- `ItemGraphNodeResponse`, `ItemGraphEdgeResponse`, `ItemGraphResponse`
- `ItemDetailResponse`
- Mutation payloads: `ReadUpdateRequest`, `StarUpdateRequest`, `NoteUpdateRequest`, `ItemTagsUpdateRequest`

### Alerts Schemas

- `AlertInterestCreate`, `AlertInterestUpdate`, `AlertInterestResponse`
- `AlertInterestPreviewRequest`
- `AlertMatchReference`, `AlertMatchEntry`, `AlertMatchListResponse`

### Other Schemas

- `TagCreate`, `TagResponse`
- Notifications:
  - `NotificationWebhookField`
  - `NotificationTemplateVariable`
  - `NotificationWebhookWrite`, `NotificationWebhookResponse`
  - `NotificationWebhookTestRequest`, `NotificationWebhookTestResponse`
  - `NotificationWebhookDeliveryResponse`, `NotificationWebhookDeliveryListResponse`
- Tagging:
  - `TaggingSettingsUpdate`, `TaggingSettingsResponse`, `TaggingSettingsBundleResponse`
  - `TaggingRuleWrite`, `TaggingRuleResponse`
  - `TaggingRulePreviewRequest`, `TaggingRulePreviewItem`, `TaggingRulePreviewResponse`
  - `TaggingReapplyRequest`, `TaggingReapplyResponse`
- `SavedViewCreate`, `SavedViewUpdate`, `SavedViewResponse`
  - `query_json` is a typed saved-view payload with `schema_version`, `version`, `rss_filters`, `alert_filters`, `windows`, and `ui`
- `ApiTokenCreateRequest`, `ApiTokenCreateResponse`, `ApiTokenResponse`
  - `ApiTokenCreateRequest` also accepts `current_password` for cookie-session step-up when creating durable API tokens from the browser
- `UserCreateRequest`, `UserUpdateRequest`, `UserAdminResponse`
- `AuditLogResponse`, `AuditLogListResponse`
- `AuditLogExportResponse`
- Stats family (`TotalsSummary`, `ActivitySummary`, `DerivedSummary`, `StatusPoint`, `DailyVolumePoint`, `FeedStats`, `DomainPoint`, and time-series schemas)
  - Includes activity heatmap contracts: `ActivityHeatmapDayRow`, `ActivityHeatmapResponse`
  - Includes signal radar contracts: `SignalRadarAxisPoint`, `SignalRadarResponse`

## Frontend Type Mirrors (`web/src/types/api.ts`)

The frontend mirrors backend contracts for all major payloads:

- Auth and users: `User`, `TokenResponse`, `UserCreateRequest`, `UserUpdateRequest`
- Tokens: `ApiToken`, `ApiTokenCreateResponse`
- Audit: `AuditLog`, `AuditLogListResponse`
- Saved views: `SavedView`
- Stats: `StatsOverviewResponse`, `StatsFeedTimeSeriesResponse`, `StatsActivityHeatmapResponse`, `StatsSignalRadarResponse` and nested types
- Feeds/import-export metadata: `Feed`, `FeedMetadataResponse`, `FeedImportEntry`, `FeedExportResponse`, `FeedImportResponse`
- Items/detail/graph: `ItemListEntry`, `ItemListResponse`, `ItemDetail`, `ItemGraphResponse`
- Tags: `Tag`
- Alerts: `AlertInterest`, `AlertMatchReference`, `AlertMatchEntry`, `AlertMatchListResponse`
- Notifications: `NotificationTemplateVariable`, `NotificationWebhook`, `NotificationWebhookWriteRequest`, `NotificationWebhookTestResponse`, `NotificationWebhookDelivery`, `NotificationWebhookDeliveryListResponse`
- Tagging: `TaggingSettings`, `TaggingRule`, `TaggingSettingsBundleResponse`, `TaggingRuleWriteRequest`, `TaggingRulePreviewResponse`, `TaggingReapplyResponse`

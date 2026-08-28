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
- `next_fetch_at: timestamptz?`
- `error_count: int`
- `last_error: text?`
- `created_at: timestamptz`

### `Item`

- `id: UUID` (PK)
- `feed_id: UUID` (FK feeds)
- `source_guid: text?`
- `url: text`
- `canonical_url: text?`
- `url_domain: string(253)?`
- `title: text`
- `summary: text?`
- `published_at: timestamptz?`
- `first_seen_at: timestamptz`
- `dedupe_key: text` (unique)
- `content_hash: string(64)`
- `status: string(32)` (default `new`)
- `last_error: text?`
- `updated_at: timestamptz`

Indexes include feed/source/canonical/domain/published/first_seen/status/content hash, feed-time composites, PostgreSQL trigram text-search helpers, and partial unique `(feed_id, source_guid)` when GUID exists.

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
- `severity: string(16)` (`low|medium|high|critical`)
- `revision: int`
- `row_version: int`
- `durable_since: timestamptz?`
- `suppression_until: timestamptz?`
- `suppression_reason: string(500)?`
- `created_at: timestamptz`
- `updated_at: timestamptz`

### Alerting V2

`AlertEvaluationRequest` is the durable, idempotent intent to evaluate one item
content hash. It records live, reconciliation, backfill, and replay provenance;
publish and processing leases; bounded retry/dead-letter state; cutover and
notification policy; accepted/evaluated counts; safe errors; and an optimistic
version. `(item_id, item_content_hash)` is unique.

`AlertEvaluationMatch` is the immutable rule snapshot accepted by an evaluation.
It retains the rule ID and revision, owner, names, categories, keywords, severity,
and suppression decision even when the live rule is later deleted. A request may
contain only one row for a rule revision.

`AlertEvaluationRequestActivity` is the append-only operator history for acceptance,
promotion, retry, failure, replay, and completion. `AlertBackfillPreview` stores an
administrator-bound, expiring candidate snapshot and its keyset continuation before
non-notifying reconciliation is applied.

`AlertOccurrence` is the durable analyst record. Its identity is unique across rule
ID snapshot, rule revision, item ID snapshot, and item content hash. Optional live
foreign keys use `SET NULL`; bounded source and rule snapshots preserve evidence.
Lifecycle state, disposition, analyst attribution, suppression, snooze, integration
event, optimistic version, and metrics-aggregation timestamps are retained.

`AlertOccurrenceActivity` stores the append-only occurrence timeline.
`AlertOccurrenceMetric` stores daily retained counts by owner, severity, lifecycle
state, and suppression after closed detailed occurrences age out.

### Investigations

`Investigation` is the versioned aggregate for a collaborative analyst collection:
title, description, `open|monitoring|closed|archived` status, severity,
`private|team` visibility, disposition, assignee, creator, and lifecycle timestamps.

`InvestigationMember` maps users to the `owner`, `editor`, or `viewer` object role.
The composite membership key is unique and deletion of a member user is restricted
until ownership and membership are deliberately reconciled.

`InvestigationEvidence` links one unique `item`, `ioc`, `report`, or
`alert_occurrence` to an investigation. It retains bounded title, description, URL,
metadata, and analyst-note snapshots instead of copying full article text or private
item notes.

`InvestigationNote` is a versioned, soft-deletable analyst note.
`InvestigationActivity` is the append-only audit timeline for aggregate, membership,
evidence, and note changes.

### Generic Integration Platform

`IntegrationInstance` stores connector configuration, encrypted secret material, health, per-instance concurrency/rate limits, and circuit-breaker state. SMTP hooks may reference another SMTP instance as a credential source.

`IntegrationRun` stores connector operations that are not event deliveries. SMTP test runs record success or failure, timing, normalized error details, and safe diagnostic metadata such as test action, recipient, draft-settings use, and the bounded SMTP server response. These runs do not enter the delivery retry or metrics state machine.

`IntegrationSubscription` maps an instance to an event type, enabled state, feed scope, structured filter, and transform. Selected feeds are normalized through `IntegrationSubscriptionFeed`.

`IntegrationEvent` is the transactional outbox record. Its unique idempotency key, routing state, attempt count, claim timestamps, and `available_at` field support crash-safe routing recovery.

`IntegrationDelivery` is the generic delivery state machine. It links an event, subscription, instance, owner, and optional replay source; stores bounded attempt/backoff state; and records terminal, dead-letter, aggregation, and last-error details. Live event/subscription pairs and delivery idempotency keys are unique.

`IntegrationAttempt` is an immutable numbered attempt for a delivery, including duration, response status, normalized error, retryability, and safe response metadata.

`IntegrationDeliveryMetric` stores hourly success, failure, dead-letter, attempt, and duration aggregates before detailed terminal history expires.

### `NotificationWebhook`

- `id: UUID` (PK)
- `user_id: UUID` (FK users)
- `name: string(255)`
- `enabled: bool`
- `event_type: string(64)` (`rss_item_new|alert_match|feed_failing|webhook_failed|daily_digest|report_ready`)
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
- `integration_id: UUID?` (generic compatibility link)
- `subscription_id: UUID?` (generic compatibility link)
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
- `delivery_state: string(16)` (`pending|sending|succeeded|failed`)
- `integration_delivery_id: UUID?` (generic compatibility link)
- durable claim, retry, attempt, source-delivery, scope-key, and idempotency fields
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
- `task_type: string(32)` (`item_enrichment|daily_brief|report|connection_test|reprocess`)
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

### Reporting Models

- `ReportTemplate`: built-in/private/shared prompt, topic, section, and default-filter configuration
- `ReportSchedule`: weekly/monthly IANA-zone cadence, source window, catch-up policy, template, filters, and delivery policy
- `Report`: immutable generation request, company/global context snapshot, period, status/stage, coverage, model usage, error, and delivery policy
- `ReportSourceItem`: ranked source metadata, tags, IOCs, bounded evidence text, citation key, inclusion state, and exclusion reason
- `ReportSection`: ordered persisted section body, key points, citations, status, and error
- `AITaskRun.report_id`: generation operation linkage used by AI activity and provider history

Report schedules use idempotent generation keys. Reports retain source, prompt, and company/global context snapshots so retries do not depend on later item or template changes. Provider credentials, model selection, and context guardrails are revalidated from current AI settings when work executes.

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
- occurrence detail, list, activity, lifecycle, snooze, and bulk-mutation schemas
- bounded backfill preview and apply schemas
- administrator evaluation detail, list, activity, replay, and metric schemas

### Investigation Schemas

- `InvestigationCreate`, `InvestigationUpdate`, `InvestigationVersionRequest`
- `InvestigationSummaryResponse`, `InvestigationDetailResponse`,
  `InvestigationListResponse`
- member add, update, candidate, and response schemas
- evidence add, response, and paginated-list schemas
- note create, update, response, and paginated-list schemas
- activity item and paginated activity schemas

### Other Schemas

- `TagCreate`, `TagResponse`
- Notifications:
  - `NotificationWebhookField`
  - `NotificationTemplateVariable`
  - `NotificationWebhookWrite`, `NotificationWebhookResponse`
  - `NotificationWebhookTestRequest`, `NotificationWebhookTestResponse`
  - `NotificationWebhookDeliveryResponse`, `NotificationWebhookDeliveryListResponse`
- Reporting:
  - `ReportCapabilitiesResponse`, `ReportPreviewRequest`, `ReportPreviewResponse`
  - `ReportTemplateCreate`, `ReportTemplateUpdate`, `ReportTemplateResponse`
  - `ReportCreateRequest`, `ReportListItem`, `ReportDetailResponse`, `ReportQueueResponse`
  - `ReportScheduleCreate`, `ReportScheduleUpdate`, `ReportScheduleResponse`
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
- Reporting: `ReportCapabilities`, `ReportPreview`, `ReportTemplate`, `ReportListItem`, `ReportDetail`, `ReportSchedule`, `ReportQueueResponse`

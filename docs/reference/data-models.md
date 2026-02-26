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

## API Schemas (`backend/app/schemas`)

### Auth Schemas

- `LoginRequest`: `email`, `password`
- `RegisterRequest`: `email`, `password(8..256)`
- `ChangePasswordRequest`: `current_password`, `new_password(8..256)`
- `TokenResponse`: `access_token`, `token_type`
- `UserResponse`: `id`, `email`, `role`, `is_active`, `created_at`

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
- `AlertMatchReference`, `AlertMatchEntry`, `AlertMatchListResponse`

### Other Schemas

- `TagCreate`, `TagResponse`
- `SavedViewCreate`, `SavedViewResponse`
- `ApiTokenCreateRequest`, `ApiTokenCreateResponse`, `ApiTokenResponse`
- `UserCreateRequest`, `UserUpdateRequest`, `UserAdminResponse`
- `AuditLogResponse`, `AuditLogListResponse`
- Stats family (`TotalsSummary`, `ActivitySummary`, `DerivedSummary`, `StatusPoint`, `DailyVolumePoint`, `FeedStats`, `DomainPoint`, and time-series schemas)

## Frontend Type Mirrors (`web/src/types/api.ts`)

The frontend mirrors backend contracts for all major payloads:

- Auth and users: `User`, `TokenResponse`, `UserCreateRequest`, `UserUpdateRequest`
- Tokens: `ApiToken`, `ApiTokenCreateResponse`
- Audit: `AuditLog`, `AuditLogListResponse`
- Saved views: `SavedView`
- Stats: `StatsOverviewResponse`, `StatsFeedTimeSeriesResponse` and nested types
- Feeds/import-export metadata: `Feed`, `FeedMetadataResponse`, `FeedImportEntry`, `FeedExportResponse`, `FeedImportResponse`
- Items/detail/graph: `ItemListEntry`, `ItemListResponse`, `ItemDetail`, `ItemGraphResponse`
- Tags: `Tag`
- Alerts: `AlertInterest`, `AlertMatchReference`, `AlertMatchEntry`, `AlertMatchListResponse`

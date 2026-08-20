# Ingestion and Processing Pipeline

This page documents worker behavior, processing stages, and internal value sets used by the backend pipeline.

## Trust Boundaries and External I/O

- Feed polling and article extraction are untrusted-input boundaries. ThreatLens applies SSRF-aware URL validation, redirect caps, byte caps, and content-type checks before processing remote content.
- Notification webhooks are a separate outbound boundary. User-configured request templates are rendered server-side, stored encrypted at rest, and retried from the saved rendered request snapshot.
- AI enrichment, daily-brief generation, and report generation are an admin-controlled outbound boundary. ThreatLens records usage data and sanitized provider-exchange metadata for those calls.

## Celery Tasks

Defined in `backend/app/tasks/feed_tasks.py`:

1. `dispatch_due_feeds`
2. `dispatch_unclassified_items`
3. `dispatch_items_missing_iocs`
4. `dispatch_feed_metadata_backfill`
5. `dispatch_new_item_notification_webhooks(item_id)`
6. `record_beat_heartbeat`
7. `backfill_feed_metadata(feed_id)`
8. `fetch_feed(feed_id, force=False)`
9. `fetch_article(item_id)`
10. `classify_item(item_id)`
11. `extract_item_iocs(item_id)`
12. `reapply_recent_item_tags(days=30, limit=0)`

## Feed Fetch Stage

### Due logic

- `fetch_mode=interval`: due when `now - last_fetch_at >= fetch_interval_seconds`
- `fetch_mode=schedule`: next cron occurrence is due (`schedule_cron` via `croniter`)

### HTTP behavior

- Uses conditional headers when available: `If-None-Match`, `If-Modified-Since`
- Retries on HTTP transport failures with exponential backoff up to 3 retries
- `304` marks feed success without parsing entries
- Non-`200` records feed failure and increments `error_count`

### Feed failure markers

`last_error` values can include:

- `unsafe_feed_url`
- `network_error:<...>`
- `http_status:<code>`

## Article Fetch Stage

### Guardrails

- URL must pass `is_fetchable_url`
- Per-domain lock via Redis key `threatlens:domain:<domain>`
- Max body size: `ARTICLE_MAX_BYTES`
- HTML-only extraction path (`content-type` must include `text/html`)

### Article error markers

`Article.error` / `Item.last_error` can include:

- `unsafe_article_url`
- `network_or_rate_limit_error:<...>`
- `response body exceeds configured cap`
- `http_status:<code>`
- `non_html_response`
- extractor errors from `extract_readable_text`:
  - `readability_error:<...>`
  - `no_extractor_succeeded`

## Classification Engine

Defined in `backend/app/services/classification.py`.

- Rules version constant: `CLASSIFICATION_RULES_VERSION = "v2"`
- Article text scoring trim: first `8000` chars after whitespace normalization

### Classification categories

- `vulnerability`
- `apt_campaign`
- `malware_ransomware`
- `phishing_social_engineering`
- `supply_chain`
- `incident_breach`
- `threat_intelligence_research`
- `defensive_guidance`
- `technology_ai`
- `multi`

### Feed priors and token rules

The classifier uses weighted regex/token rules for each category and applies feed-name priors. Primary category can collapse to `multi` when top scores are close.

### Algorithm tag sync

`backend/app/services/algorithm_tags.py`:

- Built-in algorithm tags are `CLASSIFICATION_CATEGORIES` lowercased.
- Runtime tagging settings can:
  - disable specific built-in categories
  - raise/lower minimum auto-tag confidence
  - limit how many secondary category tags are emitted
- Custom tagging rules can add arbitrary tag names based on:
  - `contains` or `regex` matching
  - `title`, `summary`, `article_text`, and/or `feed_name`
  - optional category requirements
  - optional selected-feed scoping
  - optional minimum classification confidence
- Item tags are synchronized to the current desired set, so stale auto tags are removed on re-sync.
- Manual tags are preserved and never overwritten by auto-tag sync.

## Notification Webhook Dispatch

- Notification webhook fanout covers:
  - `rss_item_new`
  - `alert_match`
  - `feed_failing`
  - `webhook_failed`
  - `daily_digest` (backward-compatible event identifier for the AI Daily Brief)
  - `report_ready`
- New-item webhook fanout is queued by `dispatch_new_item_notification_webhooks(item_id)` after feed ingestion.
- A ready AI Daily Brief writes its immutable `daily_digest` integration event in the same transaction as the brief. The compatibility scheduler only reconciles a missing current-day event and does not build a rolling RSS digest.
- A delivered ready report writes one immutable `report_ready` integration event in the report-finalization transaction. SMTP and webhook connectors route it through the same generic delivery engine.
- Deliveries are matched against:
  - enabled webhooks
  - matching `event_type`
  - all feeds or selected-feed scope
- Delivery records store the fully rendered outbound request snapshot:
  - URL
  - method
  - headers
  - query params
  - body
  - response preview
  - error/status metadata
- Saved delivery snapshots and webhook templates are encrypted at rest with `APP_DATA_ENCRYPTION_KEY`; user-facing previews are decrypted/redacted on read.
- Retries replay the stored rendered delivery snapshot instead of re-rendering from current item/feed data.

## AI Enrichment and Daily Briefing

- When AI is enabled/configured and `auto_enrich_new_items` is on, items queue AI enrichment after ingestion/classification only when they are recently published and recently first seen according to `AI_AUTO_ENRICH_NEW_ITEM_MAX_AGE_HOURS`.
- Older feed backlog is left alone unless an admin explicitly queues AI reprocess by lookback, time range, feed, count, or exact item selection.
- Item enrichment stores:
  - AI summary text
  - relevance score
  - relevance label/reasons
  - provider/model/token usage metadata
- Daily brief generation can run:
  - manually from `/ai`
  - queued in the background
  - on the configured daily schedule
- Daily brief generation:
  - selects items from the configured coverage window
  - uses item `published_at` for the coverage window when available, falling back to `first_seen_at` only for undated feed entries
  - orders by AI relevance when available
  - keeps only the configured maximum item count in the prompt
  - stores retained recent briefs and per-brief source-item logs
- AI task execution also records:
  - task runs
  - task events
  - request/response inspection data
  - usage and failure analytics

## Intelligence Report Generation

- Manual reports are accepted only after a source/context preview succeeds.
- Scheduled reports use IANA-zone calendar windows and idempotent generation keys.
- Selected source evidence, company context, and global instructions are frozen before worker execution.
- Conservative estimates reserve output, protocol framing, and a safety margin before batching bounded source excerpts.
- Evidence synthesis runs in context-safe batches; report sections run independently, with the executive summary last.
- Scope, IOC, and source sections are deterministic. AI citations are restricted to frozen `S<n>` source identifiers.
- Source count, omitted count, context estimate, batch count, model-call usage, provider exchanges, and stage transitions remain inspectable.
- The dedicated `worker-ai` consumes AI tasks at concurrency `1` by default to protect smaller local providers from parallel report/enrichment pressure.

## IOC Extraction

Defined in `backend/app/services/ioc_extraction.py`.

### Pattern families

- SHA-256: `\b[a-fA-F0-9]{64}\b`
- SHA-1: `\b[a-fA-F0-9]{40}\b`
- MD5: `\b[a-fA-F0-9]{32}\b`
- IPv4: `\b(?:\d{1,3}\.){3}\d{1,3}\b`
- CVE: `\bCVE-\d{4}-\d{4,7}\b` (case-insensitive)
- Domain: `\b(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)\.)+[A-Za-z]{2,24}\b`

### IOC source sections

- `title`
- `summary`
- `article`

### Vendor terms

- `microsoft`, `google`, `apple`, `aws`, `azure`, `oracle`, `sap`, `vmware`, `cisco`, `fortinet`, `juniper`, `palo alto networks`, `crowdstrike`, `ivanti`, `atlassian`, `citrix`, `mitel`, `okta`, `linux foundation`, `mozilla`

### Program terms

- `active directory`, `windows`, `windows server`, `microsoft exchange`, `sharepoint`, `office 365`, `defender`, `fortios`, `pan-os`, `vmware esxi`, `vcenter`, `openssh`, `openssl`, `docker`, `kubernetes`, `gitlab`, `jenkins`, `confluence`, `jira`, `wordpress`

## URL and Dedupe Utilities

### URL normalization (`url_utils.py`)

Behavior:

- Lowercases scheme/hostname
- Removes default ports (`80` for HTTP, `443` for HTTPS)
- Removes trailing slash except root
- Removes tracking params
- Sorts query pairs
- Drops URL fragment

Tracking params removed:

- Any `utm_*`
- `fbclid`, `gclid`, `mc_cid`, `mc_eid`, `ref`, `source`

Blocked hostname list:

- `localhost` (unless private fetch is allowed)

### Dedupe key strategy (`dedupe.py`)

Priority order:

1. `guid:<feed_id>:<source_guid>` if GUID exists
2. `url:<normalized_url>` if URL exists
3. `hash:<sha256(title|date_bucket|domain)>` fallback

## Connector Contract

`backend/app/services/connectors/base.py`:

- `NormalizedItem`: `guid`, `url`, `title`, `summary`, `published_at`, `raw`
- `FullTextResult`: output shape for full-text fetch implementations
- Protocol methods:
  - `poll(source_config, cursor)`
  - `supports_fulltext()`
  - `fetch_fulltext(item)`

Current concrete connector: `RSSConnector` (`connectors/rss.py`).

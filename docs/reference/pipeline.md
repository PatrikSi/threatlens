# Ingestion and Processing Pipeline

This page documents worker behavior, processing stages, and internal value sets used by the backend pipeline.

## Celery Tasks

Defined in `backend/app/tasks/feed_tasks.py`:

1. `dispatch_due_feeds`
2. `dispatch_unclassified_items`
3. `dispatch_items_missing_iocs`
4. `fetch_feed(feed_id)`
5. `fetch_article(item_id)`
6. `classify_item(item_id)`
7. `extract_item_iocs(item_id)`

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

- Valid algorithm tags are `CLASSIFICATION_CATEGORIES` lowercased.
- Item tags are synchronized to classification outputs (`primary_category` + `secondary_categories`).

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

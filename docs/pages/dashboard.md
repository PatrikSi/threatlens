# Dashboard Page

## Purpose

Unified triage workspace combining RSS intelligence, alert matches, and persistent notes in a multi-window layout.

## Top Bar Controls

- Add window dropdown:
  - `RSS Feed Window`
  - `Alerts Window`
  - `Notes Window`
  - `Daily Brief Window` (shown when AI daily briefing is enabled)
- Save current dashboard view (name + full state)
- Load saved view
- Manage views modal:
  - export all saved views to JSON
  - import saved views JSON
  - delete views
  - thumbnail previews of saved window layouts with window-type counts

## Window System

### Window types

- `rss`
- `alerts`
- `notes`
- `daily_brief`

### Snap options

- `Free`
- `Full`
- `Left Half`
- `Right Half`
- `Top Left`
- `Top Right`
- `Bottom Left`
- `Bottom Right`

### Drag/Resize behavior

- Free windows can be dragged and resized on wide layouts.
- Soft magnetic snapping during drag:
  - nearby window edge threshold: `12px`
  - viewport midline threshold: `8px`
- Snapped windows render flush (no side/bottom margin).

### Per-window controls

- Rename window
- Collapse/expand controls panel
- Remove window
- Z-order bring-to-front on click
- `Mark Seen` control for alert windows
- `What changed` badges (`+N new`)
  - RSS windows use the current user's last-open timestamp in that browser
  - alert windows use per-window seen timestamps
- RSS and alert filters operate independently per window
- Daily Brief selection is stored per Daily Brief window

## RSS Window

### Filter controls

- Feed chips (`All` + per-feed) with source-health status dots
- Tag chips (`All` + per-tag, excludes hidden tags)
- Search query
- Time range: `all`, `24h`, `7d`, `30d`, `custom`
- Custom date range (`since`, `until`)
- Read status: `all`, `read`, `unread`
- Star status: `all`, `starred`, `unstarred`
- Sort: `published_at_desc`, `published_at_asc`, `first_seen_desc`, `first_seen_asc`
- View mode: `expanded`, `compact` (default compact)
- Page size: `10`, `25`, `50`, `100`

### Item interactions

- Expand/collapse article row
- Open source link
- Mark read/unread
- Star/unstar
- Edit note
- Retry article fetch when extraction is missing or errored
- Rendered summary/article with sanitized rich text
- Optional AI insight block:
  - AI summary text
  - relevance label / score
  - relevance reasons or AI error when enrichment failed

## Alerts Window

### Filter controls

- Alert interest chips (`All` + configured interests)
- Category chips (`All` + normalized category set)
- Search query
- Time range + custom date range
- Read/star status filters
- Sort order
- View mode (`expanded|compact`)
- Page size (`10|25|50|100`)

### Match rendering

- Each item includes matched alert references:
  - alert name
  - alert category
  - matched keyword list

## Notes Window

- Freeform scratch note textarea
- Note text persists in dashboard local storage and saved views

## Daily Brief Window

- Lists retained AI-generated daily briefings
- Per-window selector for recent retained brief records
- Compact coverage header:
  - generated timestamp
  - item count
  - covered window
- Body content:
  - brief text
  - key points
  - recommended actions

## Persistence and State

- Local storage key: `threatlens.dashboard.windows.v2`
- Local storage key: `threatlens.dashboard.window-seen.v1`
- Local storage key: `threatlens.user-last-open.v1`
- Saved view payload includes:
  - per-window RSS filter state
  - per-window alerts filter state
  - Window list (type, title, rect, snap, collapse, scratch note, selected daily brief)
  - UI state (`show_advanced_filters`)

## Dashboard API Calls

- `GET /feeds`
- `GET /views`
- `GET /tags`
- `GET /alerts?include_disabled=false`
- `GET /ai/daily-briefs`
- `POST /views`
- `DELETE /views/{id}`
- `GET /items?...`
- `GET /items/{id}`
- `POST /items/{id}/read`
- `POST /items/{id}/star`
- `POST /items/{id}/note`
- `POST /items/{id}/retry-article-fetch`
- `GET /alerts/matches?...`

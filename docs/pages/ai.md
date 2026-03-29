# AI Page

## Purpose

Admin-only control plane for ThreatLens AI configuration, daily briefing, reprocessing, task operations, usage analytics, and audit history.

## Access and Visibility

- Route: `/ai`
- Nav item is shown only when:
  - the current user is an `admin`
  - `AI_ENABLED=true`

## Primary Areas

### Overview

- AI health and readiness summary
- Endpoint/model status
- Daily brief schedule summary
- Usage, latency, token, relevance, freshness, and storage KPIs
- Failure summaries and recent active tasks

### Activity / Operations

- Live queued/running AI task panel
- Task history table across:
  - `item_enrichment`
  - `daily_brief`
  - `connection_test`
  - `reprocess`
- Run detail view with:
  - status, timing, worker, model, token counts
  - parent/child progress for reprocess jobs
  - article-level child runs
  - provider exchange inspection (request / response snapshots when available)
- Daily brief source-item drilldown
- Manual action history
- Prompt-change history

### Configuration

- OpenAI-compatible endpoint base URL and model
- Timeout, completion-token, and retry settings
- Feature toggles:
  - AI summaries
  - relevance scoring
  - daily brief
  - auto-enrich new items
- Daily brief controls:
  - run time (UTC)
  - lookback window
  - max articles
  - retained brief history
- Company profile / context:
  - name
  - industry
  - regions
  - stack
  - priority topics
  - keywords
  - exclusions
  - freeform profile text
- Editable system prompt templates and instruction overlays
- Connection test
- Manual daily-brief queue action
- Scoped reprocess action:
  - lookback days
  - explicit time range
  - feed filters
  - last `N` items
  - exact item selection

## Dashboard Integration

- The dashboard can add a `Daily Brief` window when AI daily briefing is enabled.
- RSS item detail can render AI summary + relevance insight blocks when enrichment is available.

## API Calls

- `GET /ai/settings`
- `PUT /ai/settings`
- `POST /ai/test-connection`
- `GET /ai/usage`
- `GET /ai/daily-brief/latest`
- `GET /ai/daily-briefs`
- `POST /ai/daily-brief/generate`
- `POST /ai/daily-brief/queue`
- `POST /ai/reprocess`
- `GET /ai/ops/overview`
- `GET /ai/ops/live`
- `GET /ai/ops/runs`
- `GET /ai/ops/runs/{id}`
- `POST /ai/ops/runs/{id}/cancel`
- `GET /ai/ops/manual-actions`
- `GET /ai/ops/prompt-history`
- `GET /ai/daily-briefs/{id}/sources`

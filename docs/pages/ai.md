# AI Page

## Purpose

Admin-only control plane for ThreatLens AI configuration, daily briefing, reprocessing, task operations, usage analytics, and audit history.

All API paths on this page are relative to the published `/api/v1` base.

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
  - Ollama origins such as `http://192.168.0.113:11434` are treated as `http://192.168.0.113:11434/v1` for chat completions.
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
- Automatic item enrichment is limited to items recently published and recently first seen; older feed backlog is handled through the scoped reprocess action.
- The scheduler evaluates the configured daily-brief time on each UTC minute boundary and creates at most one ready brief row for a UTC date.
- When a normal scheduled or manual brief becomes ready, its integration event is written in the same database transaction. SMTP and webhook delivery is queued immediately after commit and uses an immutable snapshot of the generated brief.
- A five-minute reconciliation task recovers a ready current-day brief if immediate queue publication fails. Integration delivery retries can make a failed destination receive the brief later than its generation time.
- Historical backfill runs populate brief history without sending notifications.

## Dashboard Integration

- The dashboard can add a `Daily Brief` window when AI daily briefing is enabled.
- RSS item detail can render AI summary + relevance insight blocks when enrichment is available.

## Trust Boundary Notes

- ThreatLens sends selected item/article text, prompt instructions, and company profile context to the configured AI base URL when AI features are enabled.
- The AI endpoint base URL and model are stored in ThreatLens settings; the bearer credential comes from the server-side `AI_API_KEY` environment variable and is never sent to the browser.
- Provider-exchange inspection stores sanitized request/response metadata and token counts, while generated summaries, relevance results, and daily briefs are stored in the application database.
- Private-network AI egress is disabled by default unless `ALLOW_PRIVATE_NETWORK_AI=true`.

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

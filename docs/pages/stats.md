# Stats Page

## Purpose

Operational analytics for ingestion, extraction, feed contribution and AI usage.
Use **Ingestion statistics** and **AI statistics** to switch sections. The section
is preserved in the URL (`/stats?section=ai` or `section=ingestion`).

Ingestion requires `read:stats`. AI statistics retain the administrator role and
`read:ai` permission; AI must be enabled on the installation. An inaccessible
section keeps the navigation visible so the user can return to an allowed one.
Administrators with only AI access can discover Statistics and open its AI
section. These navigation rules do not grant permissions to either API.

The following ingestion filters and charts are independent of the AI request
window and backlog metrics.

## Filters

- Time window selector: `7`, `30`, `90`, `180` days
- Selected windows are aligned to UTC day boundaries: today plus the previous `n-1` calendar days
- Feed multi-select
- `All feeds` reset
- `Select all` shortcut

## KPI Cards

- Total Items
- Articles Extracted
- Feeds Enabled
- Items / Day (avg)

## Visual Sections

- Posts Per Feed Over Time (interactive line chart)
- Activity Heatmap (single day/hour matrix for selected time window)
- Signal Radar View (classification intensity by category)
- Derived Health metrics
- Status Breakdown bars
- Daily Volume bars
- Top Domains bars
- Feed Share bars
- Feed Contribution table

## Time-Series Chart Interactions

- Per-feed legend chips toggle line visibility
- Hover crosshair line
- Hover legend sorted by count descending
- Date range labels at chart edges
- Daily buckets use item `published_at`, falling back to `first_seen_at` when publication time is missing

## API Calls

- `GET /feeds`
- `GET /stats/overview?days=<n>&feed_ids=<csv>`
- `GET /stats/feed-timeseries?days=<n>&feed_ids=<csv>`
- `GET /stats/activity-heatmap?days=<n>&feed_ids=<csv>`
- `GET /stats/signal-radar?days=<n>&feed_ids=<csv>`

## Returned Metric Families

From `StatsOverviewResponse`:

- `totals`
- `activity`
- `derived`
- `status_breakdown`
- `daily_volume`
- `feed_breakdown`
- `top_domains`

From `FeedTimeSeriesResponse`:

- `series[]` with `feed_id`, `feed_name`, and daily `points[]`
- Scope/disclosure fields report a bounded result. The default series limit is
  100; explicit `top_feeds` accepts at most 500. The contribution table initially
  shows 50 rows and offers an explicit expansion.

From `ActivityHeatmapResponse`:

- `window_days`
- `rows[]` day rows with 24 hour columns each
- `max_count`

From `SignalRadarResponse`:

- `total`, `max_count`, `window_days`
- `axes[]` with `category`, `count`, and `pct`

## AI statistics

All overview analytics previously under **Settings → AI** now live here. AI
settings retains configuration, provider routing, prompts, jobs and history, with
a link to Statistics. The AI section includes:

- Provider/version/model usage and per-feature request outcomes.
- Known input/output/total tokens and calls whose usage was not reported.
- Successful-call P50, P95 and P99 latency and a latency distribution.
- Typed deadline, timeout, truncated-output and budget-rejection counts.
- Recorded pre-send failures and reserved provider retry attempts.
- Current queued/running work, oldest work by feature and evidence coverage.

Four linked trend charts show successful/failed requests, recorded tokens, request
latency (average and P95), and success rate. Hover a graph or use the **Trend date**
slider (arrow keys, Home and End) to inspect the same UTC date across all charts.
**View exact trend data** opens an accessible table of every daily bucket.

The charts use UTC calendar days within the rolling window, so the first and last
days can be partial. Idle days have zero requests and no success rate. Missing
latency or entirely unreported token usage appears as a gap, while recorded zero
measurements remain zero. Partially reported token totals include only the known
usage; the selected date and table disclose how many requests have unreported usage.
The latency **trend** includes all measured requests, including failures; the
successful-call reliability table and headline latency metrics exclude failures.

The request window supports 1, 7, 30 or 90 days. Coverage, current queues and
retained-history totals describe the current accessible dataset and do not inherit
ingestion feed filters. Deadline failures are included within timeout totals.
Successful-call latency percentiles exclude failed calls and missing measurements. Unknown usage
is disclosed; it is not proof of a zero-token or zero-cost request. Retry receipts
are not additional billable requests, and currency costs are not estimated.

`GET /ai/ops/overview?days=<n>` supplies the existing overview;
`GET /ai/ops/statistics?days=<n>` supplies bounded database aggregates for feature
reliability, latency, retry receipts and queue ages. Both use current authorization
and data-policy scope. A transient error retains previously loaded metrics with
an error and retry action; confirmed access withdrawal hides cached data.

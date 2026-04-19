# Stats Page

## Purpose

Operational analytics for ingestion, extraction, and feed contribution.

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
- `GET /stats/feed-timeseries?days=<n>&feed_ids=<csv>&top_feeds=8`
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

From `ActivityHeatmapResponse`:

- `window_days`
- `rows[]` day rows with 24 hour columns each
- `max_count`

From `SignalRadarResponse`:

- `total`, `max_count`, `window_days`
- `axes[]` with `category`, `count`, and `pct`

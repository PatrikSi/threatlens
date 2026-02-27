# Feeds Page

## Purpose

Manage RSS ingestion sources and scheduling behavior.

## Add Feed Form

Fields:

- RSS URL
- Name (optional; auto-detect available)
- Description
- Site URL
- Language
- Fetch mode (`interval` or `schedule`)
- Interval seconds (for interval mode)
- Cron expression (for schedule mode)

Actions:

- Detect metadata (`POST /feeds/metadata`)
- Submit new feed (`POST /feeds`)

## Feed Inventory

### Controls

- Search input
- Sort select:
  - `Newest created`
  - `Name A-Z`
  - `Name Z-A`
  - `Last fetched newest`
  - `Last fetched oldest`

### Per-feed actions

- Refresh (`POST /feeds/{id}/refresh`)
- Enable/Disable (`PATCH /feeds/{id}`)
- Switch fetch mode (`PATCH /feeds/{id}`)
- Update interval/schedule (`PATCH /feeds/{id}`)

### Visible status values

- Source health badge (`Healthy`, `Stale`, `Failing`, `Disabled`)
- URL
- Description
- Site URL
- Language
- Last fetch timestamp
- Last success timestamp
- Last error text

## Import / Export

### Export

- `GET /feeds/export`
- Downloads JSON file `threatlens-feeds-YYYY-MM-DD.json`

### Import

- File accept: `application/json`
- Accepted formats:
  - array of feed entries
  - object with `feeds` array
- Option: `overwrite existing on import`
- API call: `POST /feeds/import`

Import result displays:

- `created`
- `updated`
- `skipped`
- number of `errors`

## Access Control

- `admin` and `analyst` can mutate feeds
- `viewer` is read-only

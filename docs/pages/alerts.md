# Alerts Page

## Purpose

Manage user-specific alert interests used for keyword matching in dashboard alert windows.

## Data Model

Each alert interest includes:

- `name`
- `category`
- `keywords[]`
- `enabled`
- `created_at`
- `updated_at`

## Categories

Configured category values:

- `software`
- `vendor`
- `apt_group`
- `vulnerability`
- `malware`
- `technique`
- `campaign`
- `infrastructure`
- `other`

## UI Elements

- Create form:
  - Interest name
  - Category select
  - Keywords (comma-separated)
- `Include disabled` toggle for listing
- Grouped alert cards by category
- Per-alert actions:
  - Enable/Disable
  - Delete

## Normalization Behavior

Backend normalizes:

- category to lowercase snake style
- keywords to lowercase, trimmed, deduplicated list

## API Calls

- `GET /alerts?include_disabled=<bool>`
- `POST /alerts`
- `PATCH /alerts/{id}`
- `DELETE /alerts/{id}`

## How Alerts Drive Matching

Dashboard alert windows call `GET /alerts/matches`.

Matching searches keyword presence across:

- item title
- item summary
- item URL
- canonical URL
- classification primary category

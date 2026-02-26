# Alerts Page

## Purpose

The Alerts page lets users define intelligence interests that drive keyword matching in dashboard alert windows.

## Alert Interest Model

Each interest contains:

- Name
- Category
- Keywords (comma-separated, normalized)
- Enabled/disabled state

## Suggested Categories

- Software
- Vendor
- APT Group
- Vulnerability
- Malware
- Technique
- Campaign
- Infrastructure
- Other

## Supported Actions

- Create new alert interests.
- Toggle enabled/disabled state.
- Delete alert interests.
- Optionally include disabled entries in listing.

## Matching Behavior

Dashboard alert windows call the backend alert match endpoint, which:

- Loads active interests for the user.
- Applies keyword matching over item metadata/text fields.
- Returns matched items with per-alert match metadata.

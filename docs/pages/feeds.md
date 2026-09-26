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

Edits made while a feed save is pending remain in the draft. The accepted server
response updates fields that still match the submitted values, and later edits
keep navigation protection active. Switching records prevents an earlier save
from replacing the newly selected editor.

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

`invalid_feed_content` means the publisher returned a non-feed document or text
that cannot be stored safely, such as NUL characters or invalid Unicode character
references. The poll records a failure and uses the normal retry backoff; existing
articles remain available, and a later valid response clears the failure. Metadata
detection returns an explanatory error for the same condition. Check the feed URL
and publisher response before manually refreshing again.

Unsupported cache validators are ignored so they cannot prevent subsequent polls.
A successful response replaces the stored ETag and Last-Modified values, including
clearing validators the publisher no longer supplies. An oversized optional
language value is omitted.

## Article and Indicator Extraction

Article ingestion retains readable prose plus bounded table and code appendices.
The selected blocks are removed before prose extraction, so a nested `<pre><code>`
block and repeated appendices do not duplicate observations. A retained clipping
notice identifies appendix limits: 64 blocks, 16,000 characters per block and
128,000 appendix characters in total. Navigation, headers, footers and sidebars
are excluded from appendix selection. This does not fetch linked attachments,
render JavaScript, parse PDF files or perform OCR.

The automatic extractor recognizes MD5, SHA-1, SHA-256, IPv4, IPv6, domains, HTTP(S)
URLs, email addresses, CVEs and the existing vendor/product dictionary terms.
It accepts common defanging forms including `hxxp(s)`, `[.]`, `(.)`, `[:]`, `(:)`,
`[@]` and `(@)`. The original spelling, exact source match offsets, supporting
excerpt and applied defanging transformations are retained separately from the
normalized value. Offsets identify the saved title, summary or extracted article
text, rather than publisher HTML byte positions.

URL normalization preserves path, query and fragment case and escaping, while
normalizing scheme, host and default port. Email local-part case is preserved.
Unicode hostnames use IDNA ASCII normalization. Domain observations retain the
legacy `www.` normalization; full URLs and email domains preserve that label.
An email's domain and dotted local part are not separately classified as malicious
infrastructure. A URL can also provide a host observation, without treating names
in its path as independent domains. Extraction never requests these destinations.

Each source section is limited to 4,000,000 characters, and one extraction is
limited to 100,000 occurrences. Exceeding either limit fails the extraction without
publishing a partial replacement. Network tokens longer than 4,096 characters are
skipped whole, so a clipped prefix cannot become a different indicator. Invalid
addresses, credential-bearing URLs, scoped IPv6 URLs and invalid ports are not
accepted as full URL observations. These are conservative text parsers; no DNS
resolution, reputation lookup or maliciousness verdict is implied by a match.

Extraction runs through the existing processing queue and recovery controls.
Updating the extractor does not automatically refresh previously completed
articles. Use the processing recovery workflow for selected existing sources;
article refetch also makes their extraction eligible again.

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

# Export Page

The Export workspace builds bounded article datasets from the intelligence already stored in ThreatLens. Every authenticated user can preview and export the articles they can read. Exporting requires the `read:items` scope when a personal API token is used.

## Filters and Preview

The workspace supports:

- full-text search across article fields
- one or more feeds
- any or all selected tags
- classifications
- AI relevance labels and a score range from `0` to `1`
- read, starred, and extracted-text state
- all-time, 7-day, 30-day, 90-day, or custom date ranges
- published-date or first-seen date ordering

The default window is the latest 30 calendar days. Preview counts and rows refresh after a short input debounce. Export remains disabled while the preview is stale, unavailable, empty, invalid, or above the selected format's item limit.

The browser allows five minutes for an export download request. The bundled
nginx proxy allows 310 seconds while waiting for export response data, including
artifact preparation before headers. Configure any additional reverse proxy or
load balancer to allow at least that window. Large exports remain subject to the
configured item and uncompressed-byte limits; narrow the filters or omit full
article text if an export cannot complete within the browser budget.

Preview rows show the source, effective publication time, classification, tags, AI relevance, and IOC count. The preview is representative rather than exhaustive; `EXPORT_PREVIEW_LIMIT` controls the number of visible rows while the counters cover the complete matching result.

## Formats

### CSV

CSV is the spreadsheet-oriented default. It includes stable article and feed IDs, title, source and canonical URLs, publication and first-seen times, processing status, tags, classification and confidence, AI relevance and summaries, IOC count and flattened IOC values, and optional requesting-user state. Full article text can be included in the `full_article_text` column when needed.

The file is UTF-8 with a byte-order mark for spreadsheet compatibility. Text that could be interpreted as a spreadsheet formula is escaped. Full article text is excluded by default because it can make spreadsheet exports substantially larger.

### JSONL

JSONL writes one complete article document per line. It preserves nested classification scores, AI reasons, tag provenance, article retrieval metadata, optional full article text, IOCs, and optional requesting-user state without flattening future fields into columns. Every record carries `schema_version: 1`.

### ThreatLens Bundle

The ThreatLens bundle is a ZIP containing:

- `manifest.json` with schema version, generation time, article count, filters, selected options, and file inventory
- `articles.jsonl`
- `articles.csv`
- optional `iocs.csv`, with one extracted IOC per row

Use this format for portable research sets, backup, or downstream ingestion that benefits from both human-readable tables and complete nested records.

### STIX 2.1

STIX exports a valid STIX 2.1 Bundle. ThreatLens articles become `Report` objects. Supported extracted values map as follows:

| ThreatLens value | STIX object |
|---|---|
| IPv4/IPv6 address, domain, URL, email address, MD5, SHA-1, SHA-256 | `Indicator` |
| CVE | `Vulnerability` |
| Vendor | `Identity` |
| Program | `Software` |

Source URLs become external references, article tags become report labels, and classification confidence is converted to the STIX `0` to `100` scale. The export can apply no marking or a `TLP:WHITE`, `TLP:GREEN`, `TLP:AMBER`, or `TLP:RED` marking. This is an interoperability mapping, not a claim that every article is a validated indicator or that ThreatLens publishes directly to a TIP or SIEM.

Indicator patterns carry the `unreviewed-extraction` label and a description of
their match confidence. They omit STIX Indicator `confidence`: recognizing a
value in text does not establish maliciousness. Review the patterns before
enabling detections. Raw article exports include the shared inventory; they do
not apply a team's verdicts or suppression rules. Use reviewed, team-scoped
automation events or [reviewed publications](reviewed-publications.md) for that workflow.

### MISP

MISP exports one unpublished event per article in a MISP-compatible response document. Source URLs, tags, summaries, optional article text, and supported IOC attributes are included. The selected distribution value is written to each event, but events remain unpublished and are not sent to a MISP server.

IOC mappings include `ip-dst` for IPv4/IPv6, `domain`, `url`, direction-neutral
`email`, `md5`, `sha1`, `sha256`, `vulnerability`, `target-org`, and `text`.
Extracted attributes use `to_ids: false` and comments identify their unreviewed
provenance. Event threat level is undefined (`4`); AI relevance is not a severity
assessment. Review event quality and distribution, apply any team verdicts and
suppression rules, and explicitly enable chosen detection attributes before
publishing imported events. This replaces the earlier automatic `to_ids: true`
behavior for raw extracted values.

### PDF Bundle

The readable bundle is a ZIP with `manifest.json` and one PDF per article under `articles/`. PDFs contain source metadata, summaries, tags, classification, AI relevance, IOCs, optional requesting-user state, and optionally the full article text. It has a lower item limit because rendering and storing many PDFs is more resource intensive.

## Data and Privacy

- User state and private notes are excluded by default.
- When enabled, only the requesting user's read state, starred state, and note are exported.
- Full article text is format-specific and opt-in except for the default JSONL and ThreatLens bundle presets.
- Export filters, format, item count, size, duration, and outcome are audited. Search text, article contents, and private notes are not written to audit metadata.
- Synchronous export artifacts are generated in temporary files and removed after the response. Background jobs retain encrypted artifact chunks for their configured lifetime; download materialization uses temporary files that are removed after the response.
- Background rendering scratch is private to the effective database connection
  (endpoint, database, role and connection options) and execution claim. Cleanup
  never scans another database's namespace. Password rotation retains the same
  namespace. Legacy flat `threatlens-export-job-*` directories are not adopted:
  restart an isolated export container to clear its temporary filesystem, or
  remove those old directories only after all workers sharing that host storage
  have stopped. Changing database identity can leave an old namespace requiring
  the same deliberate cleanup. Backend test runs use independent temporary roots.

## Operational Limits

| Environment variable | Default | Purpose |
|---|---:|---|
| `EXPORT_MAX_ITEMS` | `10000` | Maximum articles in non-PDF exports. |
| `EXPORT_PDF_MAX_ITEMS` | `500` | Maximum articles in a PDF bundle. |
| `EXPORT_PREVIEW_LIMIT` | `25` | Maximum rows returned in a preview. |
| `EXPORT_MAX_UNCOMPRESSED_BYTES` | `250000000` | Maximum generated content before or after compression. |
| `EXPORT_LOCK_TTL_SECONDS` | `900` | Per-user export lock expiry and crash recovery window. Active exports renew the lock every third of this interval. |
| `EXPORT_DOWNLOAD_PREPARATION_TIMEOUT_SECONDS` | `30` | Total background-artifact download preparation allowance, including authorization, decryption, temporary writes, audit, and final access checks. |
| `EXPORT_DOWNLOAD_SCRATCH_HEADROOM_BYTES` | `67108864` | Free temporary-storage space that download admission must preserve (64 MiB). |
| `EXPORT_TRANSFER_TIMEOUT_SECONDS` | `300` | Maximum response transfer lifetime after preparation. |

Only one generated export per user can run at a time. Results are loaded in bounded batches and written to disk rather than assembled completely in memory. A changing result set, exhausted size budget, unavailable Redis lock, or competing export produces a clear failure instead of a partial artifact. Narrow filters and retry after the current export finishes.

Database batches contain at most 200 records and an estimated 8 MiB of encoded
payload, including text, metadata, tags, and IOCs. A single record above that
budget returns HTTP 413 before its body is loaded; omit article text or narrow
the selection. Full-text exports preserve the complete selected text. Previews
fetch text-availability flags without article bodies or summaries and have an
8 MiB aggregate payload guard. These are payload budgets, not exact process RSS
limits: Python objects, serialization, database drivers, and PDF rendering add
overhead. Payload growth between sizing and loading produces HTTP 409 so the
caller can refresh and retry.

## Background jobs and recovery

Use background exports for generation that may exceed the synchronous request
deadline. Acceptance is durable before broker publication and idempotent for the
same principal, request, and idempotency key. Jobs expose progress, cancellation,
failure reasons, expiry, and a download when ready. Current permissions and the
accepting credential are checked during generation and download.

Before decrypting a background artifact, download admission reserves its full
size in an anonymous temporary file. API processes sharing the temporary
directory coordinate this check with a nonblocking file lock, and filesystem
allocation accounts for reservations held by other active downloads. With the
default 512 MiB API temporary filesystem and 64 MiB headroom, only one near-limit
250 MB artifact can be prepared or transferred at a time. Smaller concurrent
downloads can use the remaining space. Reservations last through response
streaming and release on success, disconnect, timeout, failure, or process death.

Storage admission returns HTTP 503 with `export_download_capacity` and
`Retry-After: 5` when capacity is unavailable. The ready job remains intact: retry
the same download rather than creating another export. If this persists, inspect
temporary-filesystem capacity, other temporary files, and allocation support.
Linux anonymous files, process-local `/proc` descriptors, and `posix_fallocate`
support are required; unsupported storage fails before plaintext is copied.
The small `threatlens-export-download-admission.lock` coordination file contains
no article data and must not be removed while API processes are running.

Preparation uses one monotonic deadline across its transactions. SQL statements
receive the remaining allowance, and bounded decryption/write chunks check the
deadline before proceeding. Exceeding it returns HTTP 504 with
`export_download_preparation_deadline` and `Retry-After: 5`, closes partial
plaintext, and leaves the stored job available for retry. Final authorization
locks remain held during the independently bounded transfer. As with other
database operations, connection establishment and failed-network cleanup retain
their configured driver limits; a blocked kernel filesystem call cannot be
preempted between chunk checkpoints. Use the supported local tmpfs configuration
and include those driver limits when sizing end-to-end infrastructure timeouts.

The headroom protects against coordinated download allocations; unrelated
processes can still consume temporary storage. Keep container memory and tmpfs
budgets aligned, use the same `TMPDIR` for API processes sharing temporary
storage, and qualify the preparation allowance on target hardware before raising
artifact sizes or download concurrency.

Each queued job receives a durable publication reservation. If the broker's
acknowledgement is lost or consumers pause, periodic repair does not keep adding
duplicate export messages. After a 30-second grace, a newer execution canary from
the `exports-v1` queue permits repair. Missing, stale, invalid, or unavailable
canaries leave accepted work queued. Restore the export worker and Redis rather
than repeatedly recreating a queued job; existing expiry and cancellation remain
available. Running-worker crashes are recovered through the independent generation
lease and attempt budget.

Migration `0101_export_dispatch_progress` adds nullable publication markers without
expiring or regenerating existing jobs. Restart API and workers on the same release
after migration so older publishers do not bypass the reservation protocol.

## API

- `GET /api/v1/exports/capabilities` returns formats, filter options, and deployment limits.
- `POST /api/v1/exports/preview` validates filters and returns counts plus representative rows.
- `POST /api/v1/exports` generates and downloads the selected artifact.
- `POST /api/v1/exports/jobs` accepts a background export.
- `GET /api/v1/exports/jobs` lists the caller's retained jobs.
- `GET /api/v1/exports/jobs/{id}` returns progress and result availability.
- `POST /api/v1/exports/jobs/{id}/cancel` cancels a job.
- `GET /api/v1/exports/jobs/{id}/download` downloads an available artifact.

The generated [API reference](../reference/api.md#exports) and [OpenAPI document](../reference/openapi.json) define the complete request schemas.

## Reviewed team publications

The separate [reviewed publication mode](reviewed-publications.md) applies current
team verdicts, suppression, expiry and evidence revisions before approval. It
retains stable artifact identities and monotonic withdrawal updates; raw research
exports above continue to include the shared inventory.

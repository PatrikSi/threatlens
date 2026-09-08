# Background article exports

Use **Generate in background** on Export intelligence for generation that may exceed five minutes. The API stores the accepted request before publishing a worker message. You can navigate away and return to Background exports to see progress, cancel pending work, delete a ready export, or download its artifact. Existing **Generate CSV/JSONL/etc.** and `POST /v1/exports` remain synchronous and compatible.

Background jobs use the same format, item limits, text projections, and incremental artifact writers as synchronous exports. Async processing does not bypass the PDF, item, or byte limits. Filters are evaluated when execution starts; a retry reselects matching items. Completion is not a point-in-time database backup.

## API

Every operation requires `read:items`. A job belongs to its accepting human or service-account principal; knowing another job UUID does not grant access.

| Operation | Behavior |
| --- | --- |
| `POST /v1/exports/jobs` | Existing export request plus a UUID `idempotency_key`; returns durable job metadata with HTTP 202. Reusing a key and the same payload returns the original job. Different payload with that key returns 409. Capacity exhaustion returns 429 with `Retry-After`. |
| `GET /v1/exports/jobs?limit=25&offset=0` | Owner's jobs, newest first, with `items` and `has_more`; maximum page size 100. |
| `GET /v1/exports/jobs/{id}` | Status, attempts, progress, expiry, sanitized error, and download availability. |
| `POST /v1/exports/jobs/{id}/cancel` | Idempotently cancels queued/running work or deletes a ready artifact. |
| `GET /v1/exports/jobs/{id}/download` | Downloads a ready artifact after fresh authorization checks; 409 before ready or after access change, 410 after expiry or artifact loss. Responses use `Cache-Control: no-store`. |

Clients must retain the accepting idempotency key across an ambiguous POST response. The web UI reuses it when retrying the same request. After a reload, inspect the durable list before intentionally creating a new export. Tombstones remain for one additional day after expiry; keys may be reused after those rows are purged.

## Authorization and retained data

The encrypted accepting snapshot records the principal, credential ID, effective permission cap, and handling-label cap when enforcement was enabled. Execution checks the current principal and original credential at checkpoints; revocation, expiry, account disablement, and removed grants fail closed. Browser-session jobs remain subject to the accepting session's idle/absolute expiry and logout/revocation. For unattended automation, use a suitably scoped credential whose lifetime covers generation and retrieval.

Publication checks original and current access, selected item/feed membership, and source handling labels again. Download additionally checks the current requesting credential's data access. A restricted credential cannot use another credential's job to bypass handling policy. Deleted or moved source items invalidate retrieval. Revoked status/list responses omit filenames, item counts, and file sizes. Error messages contain no article text, filters, credentials, or raw exception details.

Rendering holds no global IAM or handling-policy lock. The final publication transaction fences both policies; download uses the same final fenced response boundary as synchronous exports. Policy changes during generation discard the artifact rather than publishing a partly authorized result.

Artifacts are stored as separately encrypted 256 KiB PostgreSQL chunks, accessible to API and worker replicas without a shared-filesystem requirement. Request filters and source/credential snapshots are also encrypted with the application data key. Retain previous encryption keys until outstanding jobs expire or are deleted; removing a required key makes those exports unavailable. These short-lived chunks are not an archival export store.

## Worker state, failure, and recovery

The durable state machine is `queued → running → ready`, with `failed`, `cancelled`, and `expired` terminal states. Each running attempt owns a random claim token and renewable lease. Duplicate messages cannot acquire an active claim. A stale or cancelled worker cannot append chunks or publish completion, including after lease takeover. The existing per-principal Redis export lock coordinates background jobs with synchronous exports.

`app.tasks.export_tasks.generate_export_job` runs on `processing`; `dispatch_export_jobs` runs on `maintenance` every 30 seconds through Beat. Keep both workers and Beat running. Reconciliation republishes queued rows after lost broker publication, repairs expired running leases, removes incomplete encrypted chunks before retry, expires artifacts, and removes jobs whose owners were deleted. Dispatch batches and retries are bounded. A worker crash after artifact storage but before completion leaves an invisible partial artifact that reconciliation replaces. A crash after completion does not rerender the ready job.

| Failure | Outcome and recovery |
| --- | --- |
| Broker unavailable after accepting commit | Return the accepted queued job; Beat retries publication. |
| Duplicate POST or worker delivery | Reuse the idempotent job or skip the already-owned/completed attempt. |
| Expired worker lease | Discard partial chunks and retry, up to the attempt limit. |
| Redis coordination or filesystem failure | Bounded retry with backoff; an already-busy principal waits without consuming a generation attempt. |
| Credential/policy/source change, item/byte cap, or total generation timeout | Terminal sanitized failure; create a new narrowed request after resolving the cause. |
| Cancellation/expiry during generation | Invalidate claim and remove stored chunks; the old worker cannot publish. |
| Deleted owner | Reconciliation deletes job metadata and chunks. |

Each attempt has a monotonic generation deadline, plus Celery soft/hard execution limits. The default one-hour deadline supports workloads beyond five minutes without keeping an HTTP request open. Database statements and coordination calls retain their own shorter operation limits. API status is authoritative; task result messages are diagnostic.

Local rendering scratch uses a private directory per job/claim. Normal completion cleans it immediately. Before starting more rendering on a host, workers remove directories whose durable claim is no longer active, including leftovers from hard exits. Temporary rendering/download disk and PostgreSQL WAL/backups are additional to the retained-artifact byte reservation; size deployment storage accordingly. Generation concurrency is bounded by worker concurrency and per-principal admission.

## Configuration and operations

| Setting | Default | Bound |
| --- | --- | --- |
| `EXPORT_JOB_TIMEOUT_SECONDS` | 3600 | Total generation time per attempt; Celery hard limit adds 60 seconds for shutdown. |
| `EXPORT_JOB_LEASE_SECONDS` | 120 | Worker lease, renewed at most every ten seconds. |
| `EXPORT_JOB_RETENTION_SECONDS` | 86400 | Time from acceptance to expiry, including queue wait. |
| `EXPORT_JOB_MAX_ATTEMPTS` | 3 | Actual worker attempts before terminal failure. |
| `EXPORT_JOB_MAX_ACTIVE_PER_PRINCIPAL` | 2 | Queued/running jobs per human or service account. |
| `EXPORT_JOB_MAX_RETAINED` | 1000 | Global job-row admission cap, including tombstones. |
| `EXPORT_JOB_MAX_RESERVED_BYTES` | 4000000000 | Global conservative reservation for retained encrypted artifacts; each job reserves three times `EXPORT_MAX_UNCOMPRESSED_BYTES` plus 1 MiB. |

The existing `EXPORT_MAX_ITEMS`, `EXPORT_PDF_MAX_ITEMS`, and `EXPORT_MAX_UNCOMPRESSED_BYTES` also apply. Stored file size is independently capped by `EXPORT_MAX_UNCOMPRESSED_BYTES`. Reservation release and chunk deletion occur in the same transaction. Expiry is checked during retrieval even if housekeeping is delayed; delayed housekeeping keeps its reservations and rejects additional work instead of growing storage without limit.

Investigate queued jobs by checking Beat and the `maintenance`/`processing` queues. For repeatedly interrupted attempts, inspect worker shutdown/OOM events and database/Redis availability. Structured logs report `export_job_publish_deferred` and `export_job_attempt_failed` with job UUID and exception type, without payloads. Acceptance, cancellation, and downloads are audited. Do not manually change claim tokens or mark partial jobs ready.

Regression coverage includes real PostgreSQL acceptance and idempotency, lost publication, duplicate claims, partial-artifact crash recovery, cancellation fencing, principal/credential/handling revocation before publication and download, encrypted storage, expiry, admission limits, owner deletion, and the bounded deadline beyond five minutes. UI tests cover later-page status restoration, retry key reuse, explicit download/deletion, and session changes during a download.

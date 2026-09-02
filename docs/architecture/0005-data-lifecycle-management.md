# ADR 0005: Policy-Driven Data Lifecycle Management

- Status: Accepted
- Date: 2026-09-02
- Last reviewed: 2026-09-02

## Context

ThreatLens has several independent history-maintenance jobs. Their retention
windows come from environment settings or code constants, so operators cannot
inspect, pause, preview, or selectively change them from the product. Adding a
generic table-deletion UI would be unsafe: datasets have different dependency,
rollup, authorization, and ingestion semantics.

Article retention is particularly sensitive. The `articles` row records that an
item was fetched, while the `items` row carries its ingestion deduplication key.
Deleting either row would make old feed entries eligible for ingestion or article
repair again. Reports, investigations, alerts, classifications, and other derived
records may also retain evidence independently of the fetched article body.

Backup and restore operations are host-managed workflows. Their durable run
ledger is useful activity evidence, but backup cadence is deployment policy rather
than application service health and cannot be inferred reliably by the UI.

## Decision

### Policy and target model

- A code-owned target registry defines the only configurable lifecycle targets,
  their labels, age fields, allowed retention bounds, safeguards, preview logic,
  and deletion or redaction handler. Clients cannot submit a table, column, SQL
  expression, or arbitrary selector.
- PostgreSQL is the runtime source of truth for enablement, retention, cadence,
  maximum records per run, safeguards, revision, and scheduling state.
- Existing environment retention values seed one atomic, full-catalog bootstrap
  the first time the upgraded application opens lifecycle management. A durable
  catalog marker and captured bootstrap snapshot make that transition
  auditable. After the marker is set, a missing, extra, partial, or unmarked
  policy catalog fails closed; environment values never recreate individual
  rows or bypass a disabled or edited database policy.
- Policies are independent. Disabling one dataset does not disable fixed security
  housekeeping, metric aggregation required before later deletion, or repair of
  orphaned access-policy envelopes.
- Unsafe control-plane and durable user artifacts are not lifecycle targets.
  Feeds, item identity rows, IOCs, reports, investigations, users, IAM policy,
  integration configuration, and the system operation ledger remain outside the
  configurable catalog.

### Article content

- The article policy is named **Fetched article content**. It clears the fetched
  and extracted payload in place and records when and by which lifecycle run the
  purge occurred. It retains the Article and Item identities, source URL/fetch
  outcome metadata, publication metadata, and deduplication key.
- Normal repair excludes lifecycle-purged content. A deliberate forced fetch may
  retrieve it again. The purge marker is cleared only when the fetch produces
  usable extracted text or an intentional RSS-content fallback; failures and
  empty extraction results retain the tombstone.
- Article age uses publication time, falling back to first-seen time. The default
  safeguards retain content associated with a star, note, investigation evidence,
  report source, or active alert. Execution re-evaluates safeguards while rows are
  locked.
- This policy is not represented as complete erasure: summaries, reports,
  investigations, alerts, audit evidence, backups, and other derived copies may
  remain under their own retention contract.

### Preview and execution

- A preview is server-calculated aggregate evidence tied to a policy draft,
  revision, cutoff, requesting user, and short expiry. It reports eligible and
  protected counts and age bounds without returning titles, URLs, IDs, labels, or
  content samples.
- Policy changes use optimistic revision checks. Enabling cleanup, shortening
  retention, increasing an enabled run cap, or reducing article safeguards
  requires a fresh matching preview, a reason, and explicit `PURGE`
  confirmation. Manual execution requires the same evidence plus a recent human
  browser authentication, `write:operations`, and an idempotency key.
- Preview evidence is immutable at the database boundary except for one-way,
  one-time consumption and live requester foreign-key nulling after account
  deletion. Responses include a server observation time so expiry remains
  accurate when an operator workstation clock is skewed.
- Runs snapshot the policy revision, options, cutoff, and record cap before work
  begins. A durable queued record is committed before dispatch, so broker failure
  can be recovered without losing operator intent.
- Only one queued or running execution may exist for a target. Work is processed
  in bounded, repeatable batches using locked, still-eligible rows. Each committed
  batch updates durable counters and heartbeat evidence. Retries are idempotent;
  stale leases can be reconciled; cancellation prevents later batches but cannot
  restore already committed changes.
- A terminal result distinguishes succeeded, partial, failed, and cancelled work.
  Run history retains the trigger, actor, captured policy, cutoff, aggregate
  results, retry evidence, and sanitized error information.
- Policy attribution uses a durable actor label snapshot and a configuration-only
  timestamp. Scheduler and last-run bookkeeping cannot impersonate a policy
  edit. Run request evidence and terminal evidence are likewise immutable, while
  counters can only advance.

### Authorization, audit, and UI

- `read:operations` permits viewing the catalog, aggregate previews, and run
  history. `write:operations` permits policy changes and maintenance actions after
  the endpoint's stronger browser-session checks pass.
- The Data lifecycle settings module is permission-gated rather than hard-coded
  to the built-in administrator role, allowing a custom operations role to use
  explicitly granted authority. Administrator remains its default-visible role.
- Policy mutations, manual run requests, cancellations, and terminal outcomes are
  audited with target, revision, cutoff, reason, run identifier, and aggregate
  counts. Per-record content and identifiers are not copied into lifecycle audit
  events.
- The UI presents compact grouped policies, explicit safeguards and cutoff
  semantics, server previews, asynchronous status, cancellation, and paginated run
  history. Color is never the only status cue.
- Backup, verification, restore, and restore-drill records remain in Activity.
  They no longer create System health findings or affect overall health status.

## Failure and capacity boundaries

- Deletion uses strict `timestamp < cutoff` comparisons and rechecks eligibility
  immediately before mutation.
- Batches and per-run primary-record totals are bounded to limit locks, WAL
  volume, and worker starvation. Before deleting a parent, handlers also count
  cascade and lineage dependants and enforce a fixed 10,000-row dependent budget
  per batch. An individually oversized parent is reported as protected instead
  of causing an unbounded cascade. Article redaction reports payload bytes
  removed, not immediate disk space reclaimed; PostgreSQL vacuum behavior
  remains an operator concern.
- Detail history that feeds metrics is aggregated before deletion. Existing pins,
  foreign-key restrictions, data-policy lineage cleanup, and unresolved-operation
  guards remain part of each target handler.
- Preview counts are advisory. Concurrent new holds or references can increase the
  protected result, while concurrent eligible work can change the final count.
- Lifecycle selectors have age/order indexes aligned with their cutoff queries;
  limits therefore bound mutation work without first requiring an unbounded sort
  over integration, session, article, health, audit, AI, or alert histories.
- Lifecycle execution uses the versioned `lifecycle-v1` queue and shares the
  maintenance worker in the initial release. The worker consumes both
  `maintenance` and `lifecycle-v1`; consumer presence and recent execution are
  monitored independently for each queue. Backlog age, run duration, lease
  recovery, and consecutive failure evidence must be monitored before assigning a
  dedicated worker.
- The first lifecycle deployment is a coordinated cutover, not a rolling upgrade.
  Every old publisher and consumer of `maintenance` is quiesced before migration
  and catalog bootstrap, upgraded workers are verified before Beat starts, and old
  binaries cannot be reintroduced afterward. Legacy task names remain safe
  non-deleting compatibility handlers for messages already in the broker.

## Consequences

- Operators gain selective, visible, and reversible policy configuration while
  destructive execution remains asynchronous and auditable.
- Database policy replaces environment variables as live configuration, adding a
  migration and scheduler dependency but eliminating competing deletion paths.
- Article storage can be reduced without causing re-ingestion loops, at the cost
  of retaining a small tombstone and source metadata.
- The first catalog is intentionally bounded. Complete item erasure, legal holds,
  backup lifecycle, and retention for durable investigations or reports require
  separate dependency and recovery designs rather than generic lifecycle rows.

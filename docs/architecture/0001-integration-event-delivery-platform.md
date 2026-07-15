# ADR-0001: Integration event and delivery platform

Status: Accepted

Date: 2026-07-14

## Context

ThreatLens originally delivered notification webhooks through a dedicated durable outbox and SMTP through connector-specific Celery tasks. Adding another destination required changes in domain tasks, connector storage, retry handling, and delivery reporting. Webhook configuration and history are also part of the published API and must survive rolling upgrades and downgrades.

## Decision

ThreatLens remains a modular monolith backed by PostgreSQL, Redis, and Celery. Integration execution uses these durable records:

- `integration_events` is the transactional domain-event outbox.
- `integration_subscriptions` routes event types and filters to connector instances.
- `integration_deliveries` owns delivery state, retry scheduling, and dead-letter state.
- `integration_attempts` records each external side-effect attempt.

Delivery is at least once. Event and delivery idempotency keys prevent duplicate durable work, while connector delivery IDs help downstream systems deduplicate ambiguous network outcomes.

Connector behavior is registered through an executable `IntegrationConnector` contract. Each built-in connector publishes immutable metadata and owns event routing plus delivery processing. The initial registry contains SMTP and webhook connectors; API schemas remain a closed `smtp|webhook` union until a new connector is fully implemented and supported.

Legacy notification webhook rows remain compatibility records. Existing webhook IDs, API routes, encrypted request snapshots, delivery history, and retry behavior are preserved while generic records drive execution. During the compatibility window, the legacy webhook row remains authoritative for configuration so an older process can update it safely; routing repairs the linked generic instance and subscription before creating new deliveries.

Audit logs remain immutable operator records and are not used as operational queue state.

## Consequences

- Domain writes can commit independently of Redis and external destinations.
- Connector failures cannot roll back feed ingestion or classification.
- Adding a built-in destination does not require connector-specific branching in domain tasks; it requires a registered connector, configuration/API surface, and tests.
- Webhook and SMTP delivery share bounded retries, rate limits, circuit breaking, replay, retention, and metrics.
- A timeout after a remote system accepts a request remains ambiguous; exact-once external side effects are not claimed.
- The compatibility projection adds temporary dual-write complexity and must be covered by migration and reconciliation tests.
- Connector adapters remain trusted application code. Runtime-loaded third-party plugins are deferred until several connector types validate the interface.

## Operational Invariants

- An accepted integration event is durable in the same transaction as its domain change.
- A subscription receives at most one durable live delivery for an event.
- No delivery retries forever; exhausted deliveries enter a visible dead-letter state.
- Interrupted routing and delivery claims are recoverable without operator database edits.
- A failing or rate-limited integration cannot consume unbounded worker concurrency.
- Retention never removes non-terminal events, deliveries, or attempts.
- Connector task adapters enqueue durable IDs only after their transaction commits.
- Unknown connector types are deferred during rolling upgrades instead of being dead-lettered by an older worker.

## Runtime Flow

1. A domain transaction inserts an idempotent `integration_events` row.
2. The notification worker claims the event and asks every registered connector to route matching subscriptions.
3. Routing inserts one idempotent live `integration_deliveries` row per event/subscription pair.
4. A connector claims its delivery, records an `integration_attempts` row, performs the external side effect, and finalizes both records atomically.
5. Retryable failures use bounded exponential backoff. Per-instance concurrency, rate limits, and circuit state protect the worker and destination.
6. Exhausted failures enter dead letter. Replay creates a linked delivery instead of mutating historical attempts.
7. Terminal history is aggregated into hourly metrics before retention removes eligible detail rows.

## Compatibility

The initial migration backfills generic instances, subscriptions, deliveries, and attempts without deleting legacy rows. Compatibility foreign keys remain nullable so an older application process can continue writing during a rolling deployment. Downgrading removes generic state only after detaching the legacy tables.

# ADR 0003: Operations, Investigations, Alerting V2, and IAM Hardening

- Status: Accepted
- Date: 2026-08-27

## Context

ThreatLens already has durable ingestion, bounded AI reporting, scoped API tokens,
OIDC, audit history, and a transactional integration delivery platform. Four new
capabilities must build on those boundaries without invalidating existing APIs or
making upgrades from 1.7.x unsafe:

- deployment operations and disaster recovery;
- collaborative investigation collections;
- durable alert triage and lifecycle state;
- revocable browser sessions and local multi-factor authentication.

The bundled deployment is a self-hosted modular monolith using PostgreSQL, Redis,
Celery, and a React web application. Installations may use HTTP on isolated local
networks, so WebAuthn cannot yet assume a stable secure origin and RP ID.

## Decision

ThreatLens remains a modular monolith. PostgreSQL is the authoritative recovery
state; Redis contains reconstructible transport and coordination state and is never
restored from a disaster-recovery archive.

### Operations and recovery

- The admin Operations workspace reports component health, queue progress,
  database/schema state, encryption readability, and recovery history.
- Database backups and restores are host-operated commands. The running web
  process never replaces its own database and never exposes a raw database dump.
- Backups use PostgreSQL custom-format dumps plus a versioned manifest containing
  checksums, application/schema versions, database version, row counts, and a
  non-secret encryption-key fingerprint.
- Backup integrity verification and full isolated restore drills are separate:
  parsing a dump is not described as proof that restoration works.
- Restore starts with empty Redis, revokes credentials, and quarantines outbound
  schedules and integrations until an administrator reviews the recovered system.
- `APP_DATA_ENCRYPTION_KEY` and previous keys are escrowed separately from database
  archives. They are never copied into a backup manifest.

### Investigations

- One `Investigation` aggregate represents both a collection of evidence and its
  lifecycle. There is no competing Collection and Case state machine.
- Investigations have ownership, membership, visibility, severity, assignment,
  status, disposition, notes, and an append-only activity timeline.
- Evidence links may reference articles, IOCs, alert occurrences, and reports.
  Bounded immutable display snapshots preserve provenance when source records are
  later edited or removed; full article text and private item notes are not copied.
- Object authorization is the intersection of the global role and investigation
  membership. Inaccessible and nonexistent private investigations return the same
  not-found response.
- Mutations use optimistic resource versions. Archive is the normal terminal state;
  hard deletion is exceptional and audited.

### Alerting V2

- Existing `AlertInterest` identifiers and `/alerts`, `/alerts/preview`, and
  `/alerts/matches` behavior remain supported.
- Additive rule fields and revisions extend an alert interest without rewriting
  historical evidence.
- Durable alert occurrences begin at an explicit deployment cutover. Existing
  historical query-time matches remain visible through the compatibility API but
  do not create an implicit notification flood.
- A database-backed evaluation request is the durable intent. Redis publication is
  only a wake-up, and maintenance reconciliation recovers missed or stale requests.
- Occurrences are idempotent by rule revision, item, and evidence content. A match
  and its integration outbox event commit together.
- Deduplication and suppression are separate concepts. Suppressed occurrences remain
  visible and auditable but do not emit notifications.
- Arbitrary query languages, cross-rule correlation, and hierarchical suppression
  policies are deferred until the constrained rule model has production evidence.

### IAM hardening

- New browser logins use opaque high-entropy session credentials stored only as
  hashes. Existing signed session cookies remain valid until their current expiry
  during an upgrade.
- Sessions have individual revocation, absolute and idle expiry, bounded activity
  updates, authentication-method metadata, and an operator-visible inventory.
- Local password authentication may be protected by RFC 6238 TOTP. OIDC assurance
  remains owned by the identity provider; ThreatLens does not silently add a second
  local factor to an OIDC-only account.
- TOTP enrollment is inactive until confirmed. A time step can succeed once, even
  under concurrent attempts. Recovery codes are high-entropy, stored as hashes,
  displayed once, and consumed once.
- MFA and sensitive IAM recovery actions require a browser session and recent human
  authentication. API tokens cannot satisfy step-up requirements.
- WebAuthn is deferred until HTTPS origin, RP-ID, recovery, and self-hosted routing
  requirements receive a dedicated design. TOTP is not described as phishing
  resistant.

## Security and resilience invariants

- No accepted durable work depends solely on a successful Celery publish.
- No backup is considered recoverable until an isolated restore drill succeeds.
- No restore replays stale Redis messages or automatically enables external side
  effects.
- No investigation mutation can remove its final owner.
- No alert delivery is emitted twice for the same durable occurrence.
- No TOTP time step or recovery code is accepted twice.
- Password changes, administrative MFA reset, user disablement, and disaster
  recovery revoke all active browser sessions and API tokens for the affected user.
- No administrator can retrieve another user's TOTP secret or recovery codes.

## Compatibility

Migrations are additive and retain existing identifiers and routes. New token scopes
are added without changing current defaults. Legacy JWT browser sessions are read
only compatibility credentials and naturally expire; all newly issued sessions use
the revocable store. Alerting V2 does not backfill pre-cutover occurrences unless an
administrator explicitly starts a bounded, non-notifying reconciliation.

Downgrades remove only new tables and columns after detaching optional foreign keys.
Operators must understand that investigation activity, durable alert state, session
inventory, and recovery-operation history are unavailable to an older binary.

## Consequences

- The product gains durable analyst and operator workflows without another service
  or database.
- Offline recovery is less convenient than an in-app restore button but avoids
  self-overwrite, stale-worker, secret exposure, and external replay hazards.
- Opaque sessions require a database lookup, but they enable precise revocation and
  meaningful session inventory.
- TOTP improves local password security but remains phishable; OIDC deployments
  should prefer phishing-resistant authentication at the provider.
- Keeping the computed alert API alongside durable occurrences adds temporary dual
  semantics, but it preserves compatibility and avoids historical alert floods.

## Deferred work

- WebAuthn/passkeys and provider assurance-policy mapping;
- online restore orchestration;
- custom roles and policy languages;
- public investigation sharing;
- cross-rule alert correlation and arbitrary query syntax;
- independent operations, investigation, alert, or IAM services.

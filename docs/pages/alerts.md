# Alerts

## Purpose

Alerts turn personal or team-owned keyword rules into durable, triageable occurrences while
retaining the original computed-match APIs. Rules can be evaluated against newly
classified articles, previewed against existing articles, or reconciled through an
explicit administrator backfill.

The page has three workspaces:

- **Rules** manages match definitions and previews current computed matches.
- **Occurrences** presents durable matches, lifecycle actions, suppression state,
  snoozes, activity, and bounded historical reconciliation.
- **Operations** is administrator-only and exposes evaluation health, retained
  metrics, dead-letter details, activity, and replay.

The URL retains the active workspace, filters, loaded-page search, page size,
collection page, selected occurrence, and activity page. Reload and browser
Back/Forward restore this context. **Copy triage link** shares the current
occurrence and scope; recipients still need permission to read it. If clipboard
access is unavailable, a selectable link supports manual copying.

## Rules

An `AlertInterest` belongs to one user or one named team and contains:

- `name`
- `category`
- `keywords[]`
- `severity` (`low`, `medium`, `high`, or `critical`)
- `enabled`
- optional `suppression_until` and `suppression_reason`
- immutable-history controls: semantic `revision` and `durable_since`
- optimistic-concurrency control: `row_version`

The backend trims and deduplicates keywords, normalizes categories to lowercase
snake style, and validates suppression as a paired future timestamp and reason.
Changing match semantics or re-enabling a rule increments its semantic revision and
starts a new durable cutover. Suppression and disable changes do not create a new
occurrence identity. Every actual rule mutation increments `row_version`; a PATCH
that resolves to the values already stored increments neither counter.

Supported categories are:

- `software`
- `vendor`
- `apt_group`
- `vulnerability`
- `malware`
- `technique`
- `campaign`
- `infrastructure`
- `other`

## Durable Evaluation

Classification commits an `AlertEvaluationRequest` in PostgreSQL before publishing
Celery work. The queue message is only a wake-up; a maintenance dispatcher recovers
missed publications, expired worker leases, and retryable failures. Each request
captures an MVCC-consistent snapshot of eligible rules and their matched keywords.

Evaluation creates at most one occurrence for a rule revision, item, and item-content
hash. The occurrence stores bounded rule and article snapshots so later rule, item,
feed, or classification changes do not alter historical evidence. Integration events
and occurrence notification markers commit in the same database transaction.
If an accepted rule owner is disabled or unapproved before execution, the durable
occurrence is still materialized, but no integration event is emitted for that owner.
The evaluation activity records `notification_skipped` with a safe reason code when
owner eligibility changes after acceptance. SMTP and webhook processing check the
owning account again under a database lock immediately before external I/O. Webhooks
also lock and validate the legacy delivery, generic delivery, integration instance,
and subscription after every lease renewal. A queued delivery whose owner or
integration has since become ineligible is terminally recorded as skipped and is
neither sent nor retried. System-owned integration instances remain eligible.

One owner-scoped `alert_match` event is emitted per evaluated item. Notification
payloads retain the exact match count, cap displayed rule and keyword lists, and
include at most 500 occurrence IDs. Larger evaluations set
`occurrence_ids_truncated=true`; all occurrences remain available in ThreatLens.
Suppressed matches are retained and auditable but do not generate notifications.

## Upgrade Cutover

Migration `0059_alerting_v2` timestamps every existing enabled rule. A live
evaluation is eligible only when the item's `first_seen_at` is at or after the
rule's `durable_since` timestamp. This preserves existing rules and match APIs
without producing an upgrade-time notification flood.

The migration also installs a PostgreSQL compatibility trigger on
`alert_interests`. An older application node can still insert, re-enable, disable, or
edit a rule without knowing about `revision`, `row_version`, and `durable_since`.
The trigger advances the semantic revision exactly once for definition changes or
re-enable, advances the row version exactly once for every actual mutation, and
starts cutovers only where required. Explicit V2 `+ 1` transitions are not
incremented a second time. Revision changes without a definition/re-enable transition
and row-version changes without a mutation are rejected. The trigger is removed on
downgrade.

Rule-write compatibility does **not** make mixed evaluation workers safe. V1 and V2
workers use different occurrence and notification semantics. Migration `0059`
preserves already queued legacy `alert_match` events so the compatibility router can
finish them, while a database fence rejects any new legacy event with a clear worker
upgrade error. This prevents old workers from silently starting the duplicate legacy
pipeline without discarding notifications accepted before the cutover.
Perform a coordinated worker deployment: stop classification, alert,
integration-routing, and notification workers; apply the migration; deploy the same
application version to every worker; then resume queues. Web/API nodes may overlap
briefly after the migration because the database triggers protect rule writes and
event routing, but evaluation workers must not overlap across versions.

Downgrade refuses to remove Alerting v2 while an evaluation is nonterminal or a
schema-3 alert event is still pending, routing, or retryable. Let those records
finish or explicitly dead-letter them before retrying; the guard prevents a
rollback from silently deleting accepted work that the older application cannot
resume.

Administrators can preview and apply bounded historical reconciliation. Preview
tokens are short-lived, actor-bound snapshots with keyset cursors. Applying a
preview is idempotent, never enables notifications, and may be repeated page by page.
A live classification racing the same backfill takes precedence and retains normal
notification behavior.

## Occurrence Workflow

Occurrence lifecycle transitions are forward-only:

1. `new`
2. `acknowledged`
3. `investigating`
4. `closed`

Closing requires a disposition. Supported values are `true_positive`,
`false_positive`, `benign`, `duplicate`, `informational`, and `other`. Open
occurrences may be snoozed until a future time with a required reason. Closing an
occurrence clears its snooze.

Single and bulk mutations use optimistic versions. A stale operation returns a
conflict with the current version so the UI can refresh instead of overwriting a
newer analyst decision. Bulk actions are atomic, owner-scoped, reject duplicate IDs,
and are limited to 100 occurrences.

Rule updates accept the optional `expected_row_version` field. The older
`expected_revision` request name remains a compatibility alias for the same row
version token. Clients that supply a stale value receive the coded
`alert_revision_conflict` response with the current row version and semantic rule
revision. Suppression, enable/disable, definition updates, and UI deletes all use the
row-version token. Existing clients may omit it, preserving the original API shape,
but omitted tokens intentionally do not provide optimistic conflict detection.
Unversioned PATCH and DELETE requests are deprecated: each successful mutation emits
an `alert_rule_unversioned_mutation` warning and an
`alerts.compatibility.unversioned_mutation` audit event. Clients should migrate to
`expected_row_version`; it will become mandatory in the next major API version.

Deleting a rule does not delete its occurrences. History queries use
`rule_id_snapshot`, while the optional live foreign key becomes `NULL`.

## Operations and Recovery

The administrator Operations tab shows evaluation state, source, attempts, accepted
rules and matches, created occurrences, dispatch failures, safe error details, and
the immutable activity timeline. **Needs attention** includes dead letters and
durable work whose broker publication failed.
Expired `processing` work is included when a failed broker republication was
recorded. Healthy processing rows with an unexpired worker lease are not shown as
requiring attention.

Only terminal dead-letter evaluations can be replayed. Replay requires the current
resource version, preserves the original immutable item version, and remains
idempotent for occurrences and integration events. Retry delay uses bounded
exponential backoff with jitter.

Closed occurrences older than the 180-day detailed-history window are aggregated
into daily owner, severity, lifecycle, and suppression metrics before deletion.
Metrics queries merge retained aggregates with detailed rows that have not yet been
aggregated and avoid double counting rows awaiting maintenance. Expired previews,
nonessential activity, terminal evaluations, and old metric buckets are pruned in
bounded batches. Maintenance runs every 15 minutes and performs repeated 1,000-row
sweeps, capped at 20 sweeps or 30 seconds per run. Its result reports processed
batches, elapsed time, stop reason, and remaining backlog categories; the task logs a
warning when a cap leaves work for the next run. This permits bounded catch-up instead
of limiting each category to 1,000 rows per hour.

Metrics have daily granularity. `since` and `until` are normalized to the full UTC
calendar days containing those timestamps, and `until` therefore includes its UTC
day. Live and retained rows use the same half-open UTC-day window, so a partial-day
query does not change after detailed occurrences are rolled into daily aggregates.

## Compatibility APIs

The original computed-match surface remains available:

- `GET /alerts`
- `POST /alerts`
- `PATCH /alerts/{id}`
- `DELETE /alerts/{id}`
- `POST /alerts/preview`
- `GET /alerts/matches`

`POST /alerts/preview` and `GET /alerts/matches` compute matches from current rules
and current article data. They are intentionally distinct from durable occurrence
history.

Alerting v2 adds:

- `GET /alerts/occurrences`
- `GET /alerts/occurrences/{id}`
- `GET /alerts/occurrences/{id}/activity`
- occurrence lifecycle, bulk lifecycle, and snooze mutations
- `POST /alerts/occurrences/reconciliation/preview`
- `POST /alerts/occurrences/reconciliation/apply`
- `GET /alerts/occurrences/metrics`
- administrator evaluation list, detail, activity, and replay endpoints under
  `/alerts/occurrences/evaluations`

Read paths require `read:alerts`; occurrence evidence also requires `read:items`.
Mutations require `write:alerts`, and reconciliation plus evaluation operations
require the administrator role.

## Shared team triage

Use **Queue ownership** to select personal work, all accessible queues, all team
queues, or one named team. The rule list applies this scope on the server before
its 1,000-rule response bound; each team has a separate 100-watchlist quota. Team
choices and assignment rosters expose pagination and retry failed reads.

A watchlist's owner is fixed at creation. Team watchlists can set a due delay for
new occurrences and an optional escalation delay after that due time. Changes to
these defaults leave existing occurrence deadlines unchanged. Existing personal
watchlists and links remain supported.

The occurrence queue includes assignment, overdue and escalated filters. Queue
ownership, filters, paging and the selected occurrence survive navigation in the
URL and are included by **Copy triage link**. Links confer no access; recipients
must have current team membership, API permissions and evidence access.

Members with `write:teams`, `write:alerts` and `read:items` can claim unassigned
open occurrences and release their own assignments. Team managers can assign an
eligible teammate, clear assignments, and edit deadlines. The server rechecks
membership and evidence access on every write. Closed occurrences keep their
assignment history and cannot receive new assignment or deadline changes.

Assignment and deadline drafts retain the occurrence version loaded when editing
began. Background refreshes preserve that baseline. A conflict keeps the draft
visible and offers a deliberate reload before retry; pending writes disable the
form, and failed team-access verification disables protected actions.

Escalation creates a visible queue marker and activity event for team managers.
It does not send an external email or webhook notification. The Operations tab's
30-day occurrence totals use the selected team's scope; without a named team,
they remain the current user's personal totals. Evaluation-worker diagnostics
remain system-wide administrator information.

Migration `0103_shared_triage_queues` requires the matching API and evaluation
workers before team watchlists are created. Stop classification and alert
producers/workers during the migration and replace all affected workers before
resuming queues. Old workers assume a personal owner and cannot process team
rules. The compatibility trigger versions deadline-default changes and rejects
ownership changes; it does not make old evaluation workers team-aware. See
[Named team workspaces](teams.md) for group configuration and shared ownership.

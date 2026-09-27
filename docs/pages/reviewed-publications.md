# Reviewed intelligence publications

The Export workspace has a separate **Reviewed team publications** section.
Choose a team, preview the current article filters, inspect the indicators, and
explicitly approve the selection and its distribution marking. This creates a
retained STIX 2.1 or MISP artifact. Raw exports keep their existing behavior.

Only supported network and hash indicators with a current **malicious** analyst
verdict qualify. A current review must reference the exact source and extraction
revisions and retain supporting evidence. Expired reviews, stale extraction,
reference/example values and active team suppressions are excluded. AI confidence
alone never enables a detection. Preview counts distinguish approved observations
from excluded or unreviewed observations. Two article observations of the same
indicator can appear separately in the preview; MISP combines their attributes
while preserving the individual reviews and evidence.

## Approval and access

Preview and download require `read:items`, `read:teams` and current team membership.
Creation and manual withdrawal additionally require `write:teams`. Current source
access and every historical handling label are checked on each download. Losing
membership, source access or a historical label withholds the artifact. Deleted
source articles also withhold the artifact instead of making retained evidence
public. Approval and withdrawal audit records retain the same access boundary.
Consumers must stop using an approval when current access or source retention
prevents validating it (including HTTP 403/404). A missing artifact is not proof
that its last downloaded indicators remain approved.

Standalone downloads do not create receipts. Register a publication consumer for
durable change feeds and opaque withdrawal acknowledgements after evidence access
is lost, as described below. The separate automation control stream continues to
track `automation_v1` webhook executions and their SIEM outcomes.

The preview fingerprint covers the selected evidence and reviews. A changed
source, verdict or filter produces a conflict and requires a new preview. Creation
uses an idempotency key: retry the exact request after a lost response rather than
creating another publication. Changing its request content requires a new key.

The selected TLP marking and MISP distribution are interoperability metadata;
they do not change ThreatLens handling policy or grant permission to redistribute
restricted evidence. Check your organization's distribution rules before approval.
MISP events are unpublished, but reviewed attributes have `to_ids: true`.
Consumers remain responsible for deciding whether and how to activate detections.

## Updates, withdrawals and stable IDs

Indicator identities are stable within one publication. New deliberate
publications have new identities, so a revoked STIX indicator is never revived.
A changed verdict, expired review, new suppression or changed evidence withdraws
the affected observation monotonically. Disabling a suppression later does not
reactivate an old publication. Preview and approve current evidence again.

STIX updates retain indicator IDs and set `revoked: true`. MISP updates retain
attribute UUIDs and set `deleted: true` and `to_ids: false` when all published
support for that attribute has been withdrawn. Revision timestamps advance even
for changes within one second. Review reasons, expiry, evidence, exact source
revisions and access labels remain in exported provenance.

The maintenance worker checks a bounded slice every minute; each checked active
publication becomes due again after five minutes. Larger backlogs take longer.
Downloads also reconcile the selected publication before returning it. The team
history displays revision and withdrawal counts and supports keyset pagination.
Manual withdrawal requires the displayed revision and confirmation in the UI.

**An already downloaded file cannot update itself.** Poll and reimport the latest
artifact to apply withdrawals; this mode does not send files to MISP or a SIEM.
For separately configured webhook executions, authenticated status and receiver
acknowledgement use the [automation execution protocol](automation-execution.md).
It does not acknowledge publication downloads. Preserve completed hunt
results as history even when their supporting intelligence is withdrawn.

## Bounds and deployment

A publication covers at most 100 matching articles, 250 reviewed observations and
1 MiB of evidence snapshot. Narrow the filters when a limit is exceeded; no
partial publication is approved silently. SQL projects only required metadata and
bounded evidence, never full article text. Each team retains at most 1,000
publications. Fully withdrawn publications become eligible for bounded cleanup
after 180 days; active publications are retained. Consumers must reconcile within
that history window and keep their own imported history where required.

Artifact transfers use `EXPORT_TRANSFER_TIMEOUT_SECONDS`, including stalled
clients, while retaining authorization fences until response completion. Database
preparation uses the interactive operation and lock budgets. Migration
`0115_reviewed_publications` adds the retained snapshots and access-lineage tables.
Upgrade API, maintenance worker and frontend together after running migrations.
Downgrade refuses to discard retained publications.

API paths below are relative to `/api/v1`:

- `POST /teams/{team_id}/indicator-publications/preview`
- `POST /teams/{team_id}/indicator-publications`
- `GET /teams/{team_id}/indicator-publications?limit=20&cursor=...`
- `GET /teams/{team_id}/indicator-publications/{publication_id}/download`
- `POST /teams/{team_id}/indicator-publications/{publication_id}/withdraw`

See the generated OpenAPI document for request and error contracts. Conflicts
require refreshed evidence or revision; unavailable capacity and transient lock
contention can be retried using the same request identity.

## Acknowledged consumer distribution

In **Exports → Reviewed team publications → Publication consumers**, a current
team manager can register a named consumer, subscribe approved publications and
rotate its credential. A consumer is a durable delegation capped by the registering
manager's permissions and handling access. A browser-session expiry does not erase
it. Loss of that custodian's account, team membership or required access generates
opaque withdrawals; it never silently adopts another member's clearance.

Registration returns a one-time `tlpc_` secret. Store it in the receiver's secret
manager. Send exactly one Authorization bearer header; query credentials and
duplicate authorization headers are rejected. It can only use
`/api/v1/publication-distribution/` controls. It cannot
read article text, download publications, access another consumer, or launch work.
Use a separate current scoped API credential for publication downloads. Approval
and evidence permissions are checked by the existing download endpoint.

The API registration request is
`POST /api/v1/teams/{team_id}/publication-consumers` with `name`, `expires_days`
(1–365; default90), and a stable UUID `idempotency_key`. Reusing the same key after
a lost response does not create a second registration: refresh the consumer list
and rotate its credential to obtain a new one-time secret. Reusing a key with
changed fields returns a conflict. Subscribe with
`POST /api/v1/teams/{team_id}/publication-consumers/{consumer_id}/subscriptions`
and `{"publication_id":"<UUID>"}`. Subscription retries are idempotent; withdrawn
subscriptions cannot be revived.

### Receiver protocol

1. Read `GET /api/v1/publication-distribution/status` using the consumer bearer
   token to obtain its current `generation` and `replay_floor`.
2. Persist the generation and sequence cursor locally. Initially request
   `GET /api/v1/publication-distribution/changes?generation=1&after=0&limit=25`.
   Continue using `next_after` while `has_more` is true. Pages contain stable change
   IDs, monotonically increasing sequence numbers, publication IDs/revisions and
   `available`, `changed` or `withdrawn` kinds. They contain no indicators or passages.
3. For `available` or `changed`, fetch the publication through its ordinary team
   download endpoint with current read credentials. Import its current complete
   state, including revoked STIX objects or deleted MISP attributes. A change
   notification is not independent evidence permission. Stop using a publication
   if download permission or evidence availability is lost.
4. Apply `withdrawn` by the already-known publication identity without fetching
   evidence. Preserve completed hunt outcomes as history; withdrawal does not
   authorize a new hunt or deletion of historical investigations.
5. After durable local application, POST
   `/api/v1/publication-distribution/acknowledgements` with
   `{"generation":1,"change_ids":["<change UUID>"]}`. Repeated acknowledgements of
   retained changes are safe. Store the cursor only after durable local receipt;
   a lost acknowledgement response must not cause a duplicate external action.

Background reconciliation checks bounded oldest-due batches; polling also checks
up to 50 subscriptions. Publication evidence refresh and consumer reconciliation
are eventual processes, normally on a minute schedule with five-minute source
checks. Monitor reconciliation age and unacknowledged withdrawal age; large fleets
must qualify their actual recovery capacity. A full reconciliation batch is disclosed
in the feed instead of implying every subscription was checked synchronously.

### Replay, capacity and retirement

Each team retains at most 20 consumers; each consumer retains at most 1,000
subscriptions. Acknowledged history is normally replayable for 90 days. Under capacity
pressure, contiguous acknowledged history can compact earlier above 9,000 retained
changes. The returned `replay_floor` describes the actual boundary. Unacknowledged
changes are never silently pruned. At 10,000 retained changes ordinary updates pause,
while terminal withdrawals remain admissible and fair reconciliation continues.
The feed exposes retention backpressure and recovery guidance.

A cursor below the retained floor returns `410 consumer_replay_expired`. The receiver
must discard all locally imported publications from that consumer, then POST
`/api/v1/publication-distribution/reset` with its `expected_generation` and
`discarded_previous_publications:true`. Persist the returned generation/after
position and reimport the current feed. Reset is explicit; it never assumes a receiver
has removed intelligence merely because it was offline. A receiver restoring its
own old checkpoint must perform the same recovery. Delayed acknowledgements from
the old generation are rejected. Server backup restoration has the separate
quarantine procedure below.

**Retire and withdraw** permanently withdraws the consumer's subscriptions while
leaving its credential valid for the final acknowledgements. Retired consumers
cannot reset: a reset body can survive a server rollback and reuse a numeric
generation without proving a fresh discard. Drain and acknowledge the current
change IDs instead. After all withdrawals
are acknowledged, **Archive acknowledged consumer** removes the bounded consumer
ledger, records an audit event and frees a registration slot. Publication evidence
and publication history remain governed separately. Revoke a credential immediately
when compromised; rotate it for a trusted receiver to finish outstanding withdrawals.
Revocation alone does not mean remote intelligence has been withdrawn.

The manager asks for confirmation before revocation, retirement or archival.
If a registration response is lost, retrying its identity returns an actionable
conflict; refresh the consumer list and rotate that consumer’s credential.
The secret appears only once, is never retained in the query cache, and is cleared
on navigation, session change or confirmed access loss.

### Disaster recovery

Restoring the server revokes every consumer credential and permanently retires
the restored registrations. Active reviewed-publication snapshots are withdrawn,
including their STIX `revoked` and MISP `deleted`/`to_ids` fields. The approved
evidence and external IDs remain historical. Every active subscription receives
a fresh withdrawal; all retained change UUIDs are replaced so an old queued ACK
cannot acknowledge restored obligations, even if a generation number is reused.

Keep receivers stopped during restoration and quarantine. After validation, a
team manager can rotate a retired consumer's credential to complete withdrawals:

1. Replace its credential and discard queued ACK/reset requests and paging cursors.
   Keep the imported-publication ledger and tombstones until withdrawals are applied.
2. Read `/api/v1/publication-distribution/status`. Use its current `generation` and
   `replay_floor` as the starting `after`, even when the receiver previously saw a
   higher generation or sequence. A restored sequence does not prove freshness.
3. Replay the retained changes and apply every new withdrawal UUID. Older `available`
   records are historical; downloads of those publications contain withdrawn
   indicators. Never reactivate an existing tombstone from those records.
4. Acknowledge the current change IDs. Old IDs return `404`; retired-consumer reset
   returns `409 consumer_retired_reset_forbidden` even when the number matches.
5. Archive after all acknowledgements, then explicitly register a new consumer and
   review new publications. Rotation does not revive the retired delegation.

Publications or consumers created after the selected backup may be absent from
the restored server. Receivers must discard those unmatched imports using their
own retained ledger; the restored server cannot enumerate records it never retained.
Do not erase remote hunt outcomes or stable action identities during this recovery.

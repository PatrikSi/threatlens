# Processing repair and targeted recovery

ThreatLens stores required processing versions in PostgreSQL. Redis messages wake
workers; a successful broker call is never the acknowledgement of a processing
result. Operations → Processing shows incomplete article extraction,
classification, IOC extraction and algorithm tagging, with source scope, pending
age, attempts, safe failure reasons and the next retry time.

## Select and resume work

The worklist requires `read:operations` and `read:items`; handling labels filter
rows in SQL before pagination. Recovery acceptance requires `write:operations`
and `read:items`. The initial application surface remains in the administrator's
Operations workspace. Select the exact stages to retry and review the selection.
The server checks each opaque revision again before accepting it. Refresh a
selection after a `409 processing_conflict`; replay the same idempotency UUID if
acceptance failed with an uncertain network outcome.

A run accepts at most 100 selected stages by default. All 100 may belong to one
feed: waiting entries are durable, while execution advances in bounded groups.
Runs belong to the accepting user or service account. Another principal cannot
list, read or cancel them. The accepting credential's original scope and handling
access cap remain in force; permission, credential expiry/revocation, source
membership and current handling access are checked before a worker commits.
Restricted status responses set `access_limited=true`, omit source details and
counts, and preserve safe ownership/status metadata. Losing route read permissions
returns 403. Own-run cancellation needs current `write:operations`, even when
source details are no longer available.

Cancellation takes an `expected_version`. Refresh after a conflict. It prevents
remaining selected-run stage commits and keeps already committed results counted.
Normal automatic processing can still satisfy an independent pending obligation;
that later result is not counted in the cancelled run. Cancellation or accepting-
credential expiry does not reset the automatic per-source attempt allowance. A worker
already fetching an article may finish its bounded network request before its
database changes are discarded. Cancellation cannot undo that HTTP request.
Recovery does not directly replay AI-provider calls or integration-delivery tasks.
Ordinary classification may persist the existing idempotent alert-evaluation
intent; normal downstream alert processing can produce notifications.

Automatic article repair requires an enabled feed at discovery, publication and
execution. Disabling a feed pauses existing automatic reservations with a
`feed_disabled` reason; re-enabling it resumes eligible work without resetting
its attempt allowance. The worker holds the feed state stable through an already
started, bounded automatic fetch, so disabling may wait for that attempt to finish.
An explicitly selected recovery run may fetch a disabled feed only while its
accepting credential and source access remain valid. A revoked recovery run
cannot fall back to automatic fetching on a disabled feed after restore quarantine.

## Bounds, fairness and failure recovery

| Setting | Default | Meaning |
| --- | ---: | --- |
| `PROCESSING_DISPATCH_MAX_IN_FLIGHT` |200 | Total admitted queued/running/retrying stages |
| `PROCESSING_DISPATCH_BATCH_SIZE` |50 | Maximum new admissions/publications per dispatcher transaction |
| `PROCESSING_DISPATCH_PER_FEED` |5 | Maximum admitted stages and publications per feed |
| `PROCESSING_CLAIM_LEASE_SECONDS` |300 | Claim recovery interval |
| `PROCESSING_MAX_ATTEMPTS` |5 | Automatic execution attempts per selected source generation |
| `PROCESSING_RECOVERY_MAX_ITEMS` |100 | Maximum selected stages in one run |
| `PROCESSING_RECOVERY_MAX_RETAINED` |1000 | Retained run admission bound |
| `PROCESSING_RECOVERY_RETENTION_SECONDS` |604800 | Terminal-run retention, seven days |

There can be at most two active recovery runs per principal. Waiting entries are
bounded separately by retained runs and selected stages; they consume no broker
reservation. Automatic discovery and waiting selections use one durable feed
rotation cursor plus per-feed reservations. Busy feeds cannot continuously claim
all released global slots. The dispatcher runs every 30 seconds on maintenance;
execution uses the existing processing queue. Initial ingestion wake-ups and AI or
notification queues retain their own normal dispatch policies.

Each admitted item/stage has one generation and claim token. Redelivery and old
worker messages cannot consume a replacement token. Domain changes and run
progress commit together. An actual running transaction keeps its Work row lock
until its bounded attempt ends; expired-claim recovery skips locked rows. A crash
releases the transaction lock and permits recovery after the lease. Failures
receive exponential retry delays from 60 seconds, capped at 15 minutes. Exhausted
work stays visible for deliberate operator review rather than retrying forever.
Regex timeout, input/budget limits and invalid-pattern failures require deliberate
retry after their cause has been addressed.

A published queued claim is only republished after its lease expires **and** the
processing queue execution canary has advanced beyond the previous publication.
Repeated repair ticks while every processing consumer is paused therefore do not
accumulate duplicate publications. Broker publication errors are treated as
ambiguous; they retain this claim instead of immediately publishing another copy.
Resume the processing consumer/canary to recover an uncertain or lost message.
The canary must execute on the same queue as processing work.

Database-only discovery, admission, cancellation, maintenance, local stage execution and status units use
`database_operation` deadlines. Existing outbound fetch deadlines bound article
requests; the database-only budget is not incorrectly applied across a remote
request. A stage can still spend CPU time in extraction/classification; worker
process limits and the existing bounded regex and IOC extraction protect those
paths. An expired lease rejects both claim acceptance and domain commit even before
maintenance reclaims it. It is not an absolute CPU preemption deadline.

## API and retention

- `GET /v1/processing/work`: optional `stage`, `state`, `feed_id`, `limit` and
  opaque `cursor`. Returns SQL-bounded titles without materializing article bodies.
- `POST /v1/processing/recovery-runs`: `{idempotency_key,items:[{item_id,stage,revision}]}`.
- `GET /v1/processing/recovery-runs`: principal-owned keyset pagination.
- `GET /v1/processing/recovery-runs/{id}`: resumable progress and permitted actions.
- `POST /v1/processing/recovery-runs/{id}/cancel`: `{expected_version}`.

`X-Processing-Recovery-Retention-Seconds` discloses terminal-run retention. The
same bound applies to idempotency records: after expiry, a repeated UUID is a new
admission request and its selected source revisions must still be current.
Cleanup removes at most 100 terminal runs (at most 100 entries each) per tick,
skipping runs with an active transaction still completing cancellation. Admission
and cancellation also emit the existing audit log, whose independent lifecycle
policy applies. Pending automatic domain obligations are not deleted with run
history.

Legacy pending classification/tagging timestamps start at migration time. Their
historical waiting duration is unknown. New source changes and tagging failures
record the start of the actual pending obligation; repeated failures preserve the
oldest still-pending tagging timestamp. Worklist ordering uses stable first-seen
item identity for keyset navigation; displayed age is obligation age.

Deploy migration 0091 before starting the new dispatcher/workers. Stop old Beat
before switching schedules; legacy named repair task entry points delegate to the
new dispatcher. Existing initial-ingestion messages remain safe to drain through
the normal item/source locks. Validate sustained throughput and canary freshness
on target hardware: bounded queue admission does not itself establish a capacity
or latency service-level objective.

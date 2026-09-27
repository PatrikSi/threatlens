# External execution receipts and intelligence withdrawals

A successful webhook delivery confirms HTTP transport. It does **not** confirm
that a SIEM hunt started, finished, or found anything. Typed (`automation_v1`)
intelligence and hunt deliveries now include `data.execution.id`, the destination
`webhook_id`, and a relative `callback_path`. The envelope's existing `action_id`
remains the receiver's idempotency key. Template notification bodies are unchanged.

Open **Settings → My webhooks → External executions** for receiver status,
external job ID, revision, findings, and policy acknowledgement state. `unknown`
means no conclusive receiver status has been received; it is never permission to
launch another job. A withdrawn or replaced action keeps its completed findings
and execution history.

## Receiver protocol

Use a personal scoped API token belonging to the webhook owner. Callbacks require
`write:notifications` and `read:items`; team-derived evidence also requires
`read:teams` and `read:ai`, current team membership, and every captured handling
label. Token revocation and current permissions are checked before mutation.
The execution ID alone grants no access. Personal ownership is unchanged; service
account/team ownership of webhook destinations is a separate feature.

Paths in this protocol are backend-relative (`/v1/...`). The bundled web proxy
mounts them under `/api/v1/...`: prepend `/api` to the supplied `callback_path`
when using that proxy. A custom ingress must preserve its configured API prefix.

1. Verify the webhook signature over the **exact bytes** received.
2. Persist `(webhook_id, action_id)` and the accepted request before returning HTTP
   202. Use a uniqueness constraint, not a process-local cache.
3. Before launching a vendor job, look it up by this key. If launch acknowledgement
   is lost, reconcile the existing job; do not launch a second one automatically.
4. POST a receiver receipt to the supplied callback path, for example:

   ```json
   {
     "callback_id": "0367b50a-4d6f-49a2-b54b-260376a38923",
     "sequence": 1,
     "external_job_id": "siem-job-431",
     "status": "accepted",
     "findings": null
   }
   ```

5. Persist each callback before sending it. Retry **the same callback ID and
   content** if its HTTP response is lost. Exact duplicates succeed without a
   second transition; conflicting reuse, an older sequence, a changed job ID,
   and terminal-state changes return HTTP 409.

Statuses are `accepted`, `running`, `completed`, `failed`, and `unknown`.
Sequences increase for new receipts; gaps are allowed. Running cannot return to
accepted, including through an intermediate unknown status. Completed/failed
receipts are final. Up to 8,000 characters of storage-safe findings may accompany
`completed`; unsupported Unicode and NUL are rejected before persistence.

Known pre-send failures and retryable HTTP rejections retain normal delivery
retries. A tracked action with ambiguous transport or worker-crash outcome waits
for receiver reconciliation instead of being republished automatically. Manual
replay preserves the original action ID, and the receiver must deduplicate it.

## Policy changes are a separate durable control stream

The maintenance worker examines at most 100 due execution snapshots every minute.
Each row retains its next check time, with a five-minute normal revisit interval;
large backlogs therefore take multiple sweeps. Row locks use `SKIP LOCKED`, so an
active callback cannot block the scan. Source/extraction revisions, approval and
team context, the owner’s durable permissions, current team membership and handling
label grants, analyst verdicts, verdict expiry, and team suppression changes can
invalidate an earlier team-scoped action. Shared raw intelligence is not changed
by a different team's policy. Interrupted scans safely resume from committed
state. No external job is started during reconciliation.

An invalidated action receives exactly one durable `intel.withdrawn` update, or
`intel.replaced` when another matching current action has already been routed to
the same destination. The replacement ID is advisory: receivers still require
the independently authorized new webhook before starting work. A policy update
never changes a completed execution into a new job or deletes its findings.

Receivers **poll** this control stream; these updates are not new outbound
subscription types and do not reuse the original subscription's filters:

- `GET /v1/notifications/automation/updates?limit=100` returns unacknowledged
  records plus a bounded pagination cursor. Continue using `after`, and restart
  from the first page on the next polling cycle to discover concurrent inserts.
- Apply withdrawal/replacement locally, keeping a durable tombstone even if the
  original webhook has not arrived yet. A late delivery must not reactivate it.
- `POST /v1/notifications/automation/updates/{id}/ack` acknowledges local
  application. Acknowledge revisions in order for each execution; skipped earlier
  revisions return HTTP 409. Retry acknowledgement safely after a lost response.
- `POST /v1/notifications/automation/reconcile` requests another bounded scan of
  the owner's **due** rows; it does not bypass the recorded revisit time.

Read access to this feed requires `read:notifications`; acknowledgement and
manual reconciliation require `write:notifications`. The feed contains only prior
opaque action/destination/execution IDs, generic reasons, and policy revisions.
It deliberately contains no indicator values, source details, or findings, so an
owner who loses access to the original evidence can still remove that evidence
from their receiver. Revoked credentials cannot consume or acknowledge updates.

The frontend displays pending policy acknowledgement separately from external
execution status. Receivers should alert on prolonged polling/callback failures.
Deleting webhook configuration retains its execution receipts and source event;
normal event cleanup skips source events referenced by these receipts. Deleting
the personal owner removes their receipts. Plan offboarding and receiver cleanup
before revoking the last working token. These receipts currently require explicit
administrative retention planning; they are not silently expired with ordinary
HTTP-delivery history.

## Findings and investigations

A completed result can be attached to an existing investigation from the execution
dialog. The API is
`POST /v1/notifications/automation/executions/{id}/findings`, with
`investigation_id`, `expected_investigation_version`, and `expected_sequence`.
It requires current `write:investigations`, destination/source access, and
`write:teams` for team investigations. Archived investigations reject writes.
All captured evidence labels are merged into the investigation before commit;
attachment is allowed only once and stale versions return HTTP 409.

## Runnable reference receiver

[The standard-library receiver](../../examples/automation-receiver/receiver.py)
uses a durable SQLite WAL ledger, verifies ThreatLens HMAC signatures, deduplicates
accepted actions across restarts, replays lost callback acknowledgements, and
consumes/acknowledges withdrawals independently of failed execution callbacks.
The default `serve`/`sync` modes record queued jobs and protocol receipts locally.
The explicit [OpenSearch connector](opensearch-connector.md) adds bounded approved
hunt searches, status polling and withdrawal handling. Other vendors require an
adapter with equivalent stable-action and ambiguity protections.

Set `THREATLENS_SIGNING_SECRET` to the signing secret in the webhook's credential
profile. Start behind your authenticated TLS reverse proxy:

```bash
python3 examples/automation-receiver/receiver.py serve --database receiver.sqlite3
```

The example requires Linux/POSIX and its main thread so it can enforce absolute
transfer deadlines with process alarms. It refuses an already active process alarm
or threaded use. It listens only on `127.0.0.1:8091`, accepts bounded signed POST
bodies, and allows five minutes of timestamp skew. Incoming requests and individual
API transfers have a 15-second total deadline, including headers and body. A sync
invocation has a 60-second network budget and applies policy withdrawals before
uploading status receipts; pending work remains durable for the next invocation.
Synchronize every minute using your scheduler. Set `THREATLENS_URL` to the API
base **before `/v1`**, including the proxy prefix: for the bundled web proxy use
`https://threatlens.example/api`; for a directly exposed backend use its origin,
such as `http://127.0.0.1:8000`. The client appends `/v1` itself. Set
`THREATLENS_API_TOKEN` to a scoped owner token or a
[team destination receiver credential](team-integrations.md):

```bash
python3 examples/automation-receiver/receiver.py sync --database receiver.sqlite3
```

`sync` retries locally pending callbacks and polls policy updates; it does not
query a SIEM or discover remote job status. After your adapter reconciles a vendor
job, record a status with the execution ID. Deliver the
previous pending callback before advancing status:

```bash
python3 examples/automation-receiver/receiver.py status --database receiver.sqlite3 \
  --execution-id 2efdf73a-4d89-49ed-a396-d383f71d1ee7 --status completed \
  --findings 'Analyst-reviewed result summary'
```

Keep the SQLite database on persistent private storage. Run one receiver process
for this reference implementation; a production clustered receiver should use a
shared transactional database and vendor-specific reconciliation. API redirects
are refused so a destination cannot redirect the scoped credential elsewhere.

For subscription matching, see [same-indicator conditions](indicator-conditions.md).

## Disaster recovery

A restored database may be behind the receiver's durable ledger. The recovery
quarantine hook therefore changes nonterminal receipts to `unknown`, preserves
completed/failed history and findings, and issues a fresh withdrawal ID for every
previously current action. Existing policy acknowledgement records remain
historical; an acknowledgement for an older update cannot acknowledge the new
withdrawal. Reapplying quarantine is idempotent and never launches remote work.

After recovery, rotate the revoked integration credentials, poll and apply the
withdrawal stream, then reconcile each existing job by its stable action/job ID
against the receiver or SIEM. Submit newly observed status using a new callback
ID and a sequence higher than both retained ledgers. Do not interpret `unknown`
as permission to replay a hunt. A rollback may reuse numeric policy revisions:
deduplicate policy updates by their UUID, apply every new withdrawal UUID, and
retain tombstones rather than assuming a previously seen revision is an ACK.


### Receiver retry scheduling

The reference receiver upgrades existing SQLite ledgers in place. Callback and
policy acknowledgement attempts have durable exponential backoff (five seconds
through one hour, with stable jitter), oldest-attempt ordering and independent
bounded batches/time slices. Attempts are recorded before network I/O, so a crash
or lost response replays the same callback or acknowledgement after its delay.
A failed first page does not block later withdrawals: control-feed traversal is
persisted independently from acknowledgement success and wraps after the final
page. Run `sync` periodically; one run processes at most 100 entries per lane.
Neither retries nor timeout recovery authorize another hunt launch.

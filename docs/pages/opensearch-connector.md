# OpenSearch hunt connector

The reference receiver includes an OpenSearch connector for approved,
indicator-based hunts. It uses the OpenSearch asynchronous search API, retains a
separate action ledger in OpenSearch, polls search status, reports bounded findings
and cancels a known running search before acknowledging a withdrawal. It does not
create monitors, trigger notification actions, or execute AI-authored query text.

## Configuration

Use an OpenSearch installation with the asynchronous search plugin. Give the
connector access to only the intended log indexes, its dedicated action-ledger
index, and asynchronous search submit/read/delete APIs. The standard connector
requires HTTPS, normal CA verification and an explicit authorization header; it
rejects redirects and caps each transfer at 15 seconds and one megabyte. Configure
an approved private CA using the runtime's normal trust store, not disabled TLS
verification. Protect both the receiver SQLite file and configuration/credentials;
new and upgraded receiver databases use mode `0600`.

Create a local configuration file:

```json
{
  "ledger_index": "threatlens-hunt-actions",
  "indices": ["security-logs-*"],
  "timestamp_field": "@timestamp",
  "indicator_fields": {
    "domain": "destination.domain.keyword",
    "ipv4": "destination.ip",
    "sha256": "file.hash.sha256"
  },
  "lookback_hours": 24,
  "allowed_handling_label_ids": ["REPLACE_WITH_APPROVED_LABEL_UUID"],
  "team_id": "REPLACE_WITH_TEAM_UUID"
}
```

Use fields that actually exist in your telemetry mappings. Index names/prefixes,
fields, team and handling labels are explicitly constrained. The connector accepts
only `hunt.approved` events with accepted review state, complete indicator coverage
and **malicious analyst verdicts**. Excluded, benign and reference indicators are
omitted. Unknown/inferred maliciousness alone cannot launch a search. It constructs
literal term predicates and a fixed time window ending at the event timestamp;
indicator strings never become a query language or script. Queries retrieve at most
20 document IDs/index names, no source documents, and count at most 10,000 matches
with the returned count relation preserved. The maximum configured lookback is
seven days and the search timeout is 60 seconds.

Set `THREATLENS_URL`, `THREATLENS_API_TOKEN`, `THREATLENS_SIGNING_SECRET`,
`OPENSEARCH_URL`, `OPENSEARCH_AUTHORIZATION` and `OPENSEARCH_CONNECTOR_CONFIG`.
`OPENSEARCH_AUTHORIZATION` is the complete header value for the authentication
mechanism enabled on your cluster. Use a destination-scoped receiver token for
ThreatLens. Serve the signed webhook as described in the
[receiver documentation](automation-execution.md), then run periodically:

```sh
python3 examples/automation-receiver/receiver.py opensearch-sync --database receiver.sqlite3
```

Each run synchronizes policy and callbacks, then checks at most three eligible
jobs with independent next-check times. It finishes the bounded policy-feed
traversal before launching, and pending withdrawal acknowledgements block new
launches. Existing legacy SQLite jobs without captured payloads are not
retroactively launched. Duplicate signed deliveries reuse their original action.

## Crash and withdrawal semantics

A deterministic action document is created before submitting a search. Only the
process that successfully creates it can submit. Existing documents are looked
up, never submitted again. Compare-and-set updates protect concurrent state
changes. Callback `external_job_id` identifies this stable action record; its
OpenSearch asynchronous search ID is retained in the record and included in final
findings. Keep the action ledger for as long as an action can be replayed and
back it up with the receiver database. Deleting either ledger without a recovery
plan can weaken deduplication.

OpenSearch does not provide lookup of an asynchronous search by ThreatLens's
caller action ID. If acceptance succeeds but the response containing its async ID
is lost before durable capture, the connector reports **unknown** and keeps a
launch tombstone. Repeated synchronization will not launch a second search.
An operator must find the accepted search using cluster audit/operational evidence,
verify its exact query and source scope, then attest to the recorded query digest:

```sh
python3 examples/automation-receiver/receiver.py opensearch-bind \
  --database receiver.sqlite3 --execution-id EXECUTION_UUID \
  --external-search-id VERIFIED_ASYNC_SEARCH_ID \
  --confirm-query-digest RECORDED_QUERY_SHA256
```

The command verifies that the search exists and that the supplied digest matches
the unresolved ledger entry. **The operator must establish that the external ID
belongs to that exact query**; the vendor response does not prove that association.
Normal polling/withdrawal can then resume. Missing/expired search results remain
unknown and never cause resubmission. If original configuration changes while a
job is outstanding, restore its accepted query configuration before polling.

Withdrawals create remote tombstones even when a job was not launched yet. Known
running searches are cancelled; unknown accepted searches require reconciliation
before acknowledgement. Completed findings remain immutable history. A
replacement requires a separately approved action. Partial/timed-out/shard-failed
searches are not reported as successful completed hunts.

## Validation and deployment qualification

Unit tests exercise lost acceptance responses, restarts, conflicting action/query
reuse, withdrawals, exact typed predicates, exclusions and malformed responses.
A local OpenSearch **3.8.0** container also exercised real launch/poll/findings,
completed-history withdrawal and lost-response recovery by explicit binding.
The [measurement artifact](../reviews/capacity/2026-09-27-opensearch-connector.json)
records two distinct actions and exactly two external launches. Reproduce against
an isolated, unauthenticated loopback test container:

```sh
backend/.venv/bin/python scripts/operations/qualify_opensearch.py \
  --url http://127.0.0.1:TEST_PORT
```

The harness creates and removes two uniquely named indexes. It refuses non-loopback
targets. This validates vendor API contracts, not your TLS, roles, index mappings,
production volume, backup recovery or SIEM outcome quality; qualify those in the
intended deployment before enabling automatic hunts.

The `OpenSearch connector vendor contract` quality gate repeats this test against
pinned OpenSearch 3.8.0 on each quality-gate run. Its disposable service exposes
only loopback HTTP, with a 512 MiB Java heap, 2 GiB memory limit, two CPUs and a
512-process limit. The standard-library-only harness has a two-minute deadline;
GitHub removes the service when the job ends. Qualification output and service
logs are retained for 14 days, including failed runs. This checks the vendor API
contract and duplicate-launch safeguards; it does not establish production
security or sustained capacity.

API references: [asynchronous search](https://docs.opensearch.org/latest/search-plugins/async/index/)
and [document create](https://docs.opensearch.org/latest/api-reference/document-apis/index-document/).

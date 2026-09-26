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
For authenticated execution status and receiver acknowledgement use the separate
[automation execution protocol](automation-execution.md). Preserve completed hunt
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

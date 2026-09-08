# Investigations

## Purpose

Investigations are collaborative analyst workspaces for collecting and preserving
the evidence behind a threat assessment. An investigation keeps lifecycle state,
severity, assignment, membership, notes, evidence snapshots, and an activity trail
under one stable identifier.

## Workflow

1. Create an investigation with a title, severity, and private or team visibility.
2. Add owners, editors, or viewers and optionally assign an owner or editor.
3. Attach articles, IOCs, reports, or durable alert occurrences as evidence.
4. Record analyst context in notes and use Activity for the chronological audit
   trail.
5. Move the investigation through `open`, `monitoring`, `closed`, and `archived`.
   Closing can include a disposition; archived investigations are hidden from the
   default queue but remain available to authorized users.

The list workspace supports text, status, severity, assignment, and archive
filters. Filter and page state are encoded in the URL so a queue view can be
bookmarked or shared with another authorized analyst.

## Evidence

Evidence can reference these source types:

- `item`: an ingested article or feed item
- `ioc`: an extracted indicator of compromise
- `report`: a generated threat report
- `alert_occurrence`: a durable Alerting v2 occurrence

The evidence finder uses human-readable discovery instead of requiring an analyst
to know a source UUID. With an empty all-source search it suggests recent accessible
articles, reports, and alert occurrences without running a high-cardinality IOC
inventory; selecting Indicators explicitly still browses recent IOCs. Entering a
query searches article titles and URLs, indicators, reports, and alert occurrences.
Select a 24-hour, 7-day, 30-day, or 90-day observation window and one source family,
or search all accessible sources, to bound the search. ThreatLens recognizes URLs,
IPv4 and IPv6 addresses, domains, CVEs, and common hash lengths server-side, shows
the detected indicator type, and correlates IOC matches with recent accessible
articles. A plain text query remains a normal title/value search, while an exact
UUID is accepted as a technical fallback. Exact UUID
lookups address the stable source ID directly, so they are not limited by the
selected observation window; investigation membership, source permissions, data
labels, and source ownership are still enforced.

Candidate counts stop after 1,000 matches per source. When a source exceeds that
bound, the finder labels the result count as a lower bound and asks the analyst to
narrow the query, source scope, or time range instead of performing an unrestricted
count over the tenant's retained corpus.

Search results display source names, observed time, match reason, related article
context, and whether the record is already attached. Stable source IDs are kept in
the technical-details disclosure and used only after the analyst selects a result.
Unavailable source families are shown as permission-locked capabilities; they do
not return result counts, titles, or timing metadata. Candidate results are hints,
and the attach request revalidates investigation membership, source permissions,
data labels, source ownership, and the investigation version before storing a
snapshot. A search failure does not hide evidence already attached to the
investigation, and failed attachment preserves the analyst's selection and context
note for a safe retry.

Candidate discovery uses a `POST` body so analyst-entered URLs, indicators, hashes,
and free-text terms do not appear in normal reverse-proxy or application access-log
query strings. Searches are read-only and do not attach evidence until the analyst
confirms a selected result.

Adding evidence requires both investigation write access and read access to the
source type. ThreatLens stores a bounded display snapshot with the source ID,
title, description, URL, and selected metadata. It does not copy full article text
or private item state into the investigation. The snapshot remains useful if the
source is later edited or removed, while the original source ID preserves
provenance.

## Access Model

Global roles and object membership are both enforced:

- Administrators and analysts can author investigations when their API token has
  the required investigation scopes.
- Owners can manage the investigation and its membership.
- Editors can update content, evidence, notes, and lifecycle state.
- Viewers have read-only access.
- Team-visible investigations can be discovered by eligible team members; private
  investigations require membership.

The final owner cannot be demoted or removed. Deactivating, demoting, or deleting a
user is blocked when that user is the final owner of an investigation, and active
assignments are reconciled as part of the same transaction. Inaccessible private
investigations return the same not-found response as nonexistent records.

Every write revalidates the actor's active, approved analyst/administrator state
under the same database lock used by IAM access reductions. A write already waiting
when access is reduced is rejected with `investigation_actor_not_eligible`; it cannot
commit using stale authorization. Read requests use the authorization snapshot for
that request, while session revocation prevents subsequent requests.

## Concurrent Changes

Investigation, membership, evidence, and note mutations carry an expected resource
version. If another analyst saves first, the API returns a coded `409` conflict and
the UI reloads current data instead of silently overwriting either change. Retry a
mutation only after reviewing the refreshed record.

## Failure States

- Source-not-found and source-permission failures do not create partial evidence.
- Duplicate evidence is rejected with a conflict and leaves the existing snapshot
  unchanged.
- A failed mutation rolls back its activity entry and all aggregate changes.
- Truncated note and activity collections are labeled; older activity remains
  available through the paginated activity endpoint.
- Activity foregrounds evidence titles, member email addresses, and readable
  changes; stable IDs, event codes, exact timestamps, and payloads remain available
  in a technical-details disclosure for correlation.
- Every successful mutation records both investigation activity and an
  administrator audit event where applicable.

## API Surface

The published API uses the `/api/v1/investigations` base path. The backend service
also exposes the same routes at `/v1/investigations`.

- `GET, POST /investigations`
- `GET, PATCH /investigations/{id}`
- `GET /investigations/member-candidates`
- `POST, PATCH, DELETE /investigations/{id}/members[...]`
- `POST /investigations/{id}/evidence-candidates`
- `GET /investigations/{id}/evidence`
- `POST, DELETE /investigations/{id}/evidence[...]`
- `POST, PATCH, DELETE /investigations/{id}/notes[...]`
- `GET /investigations/{id}/activity`

See the generated [API reference](../reference/api.md) for request and response
schemas.

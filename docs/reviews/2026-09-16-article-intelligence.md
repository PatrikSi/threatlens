# Article and team intelligence implementation review

Date: 2026-09-16

## Scope

Shared article extraction now identifies entities, product versions, indicator
roles and relationships with supporting passages. Each team has a versioned AI
context and a separate article assessment. Optional hunt cards support analyst
review and creation of a team investigation with the reviewed evidence.

Both new feature switches default to off. Team assessments use the existing
article provider route and its legacy fallback, model capabilities, deadlines,
budgets and durable provider receipts. No additional environment variables are
required. Deploy matching API, web and AI workers before enabling the features.

## Reviewed boundaries

| Area | Enforced behavior |
| --- | --- |
| Source evidence | Bounded schemas, recognized entity/indicator roles, local relationship IDs and exact supporting-passage matches are validated before provider success settlement. |
| Team privacy | Current group membership, credential permissions and article access apply independently. Prior results retain their original source-label snapshot across relabels and regeneration. |
| Asynchronous work | Accepting credential, team/context revision, source revision, current delivery and cancellation are rechecked before provider I/O and publication. |
| Provider recovery | Receipts belong to the team assessment. One team's uncertain request does not block another team on the same article; the original assessment still requires reconciliation before unsafe replay. |
| Worker loss | Actual scheduler selection covers team assessments. Safe pre-request work receives a replacement delivery; unresolved or successful external I/O is not automatically repeated. |
| Review | Optimistic versions protect notes and acceptance. Creating an investigation requires an accepted current hunt and investigation write access. Archived result revisions and created investigation notes are preserved. |
| Browser lifecycle | Session-local drafts survive collapse, navigation and transient verification failures. Confirmed access loss hides protected results; stale query responses cannot replace completed mutations. |
| Capacity | Generation is explicit, with installation/user queue admission limits. Source selection, output contracts and context budgets are bounded. Inventory scans stream retained ciphertext and apply a shared limit to operations previews. |
| Operations | Task/usage detail and failure history retain team membership checks. Current assessment task references survive ordinary history pruning. Encryption health covers current and archived source-authority snapshots. |
| Compatibility | New columns/defaults preserve existing summaries and settings. Older settings clients retain omitted switches. Downgrade locks enforce an offline check and reject retained feature data. |

## Validation

The frontend suite passed all 1,179 tests in 137 files. The production build,
lint and source-size checks passed. Two browser workflows passed in Chromium,
Firefox and WebKit (six cases), covering keyboard use, session failure recovery
and scoped automated accessibility checks with no violations.

PostgreSQL integration tests exercise source-label changes, credentials and OIDC
expiry, concurrent requests, failed publication, worker replacement, receipt
isolation, review conflicts, retained encryption keys and lifecycle pruning.
Generated API reference and OpenAPI documents match the application contract.

All four disposable-container recovery tests passed against the final backend
image. They cover backup verification, recovery drills, failed-restore rollback,
successful destructive restore, both legacy database-role upgrade scenarios,
and an upgrade/downgrade/upgrade migration round trip with separate database
roles. The restored team profile retains its fields, version and editor, while
recovery quarantine disables both new AI switches. The test harness now waits
for the final PostgreSQL TCP server instead of mistaking the temporary
initialization server for readiness. Disposable resources were removed by
teardown; the running local application was unchanged.

The full backend regression run passed 3,364 tests, with two skipped, in
18 minutes 45 seconds. Seven additional receipt-isolation tests passed with
coverage appended to the full run. Combined line/branch coverage is **86.19%**;
reporting coverage is **86.61%**. `scripts/check_coverage.py` passed every
existing overall, reporting and critical-module threshold without reducing any
minimum. Backend Ruff checks and the source-size gate for 821 production files
also passed.

## Limits and follow-up work

- Exact quotes establish traceability; they do not prove that the publisher or
  model is correct. Analysts still review reported claims and AI inference.
- Extraction uses the established bounded article excerpt. Team assessments can
  shrink their separate excerpt further to fit the selected model's context.
  Omitted content is disclosed; these features do not promise whole-document
  understanding.
- Team inventory and telemetry are manually maintained. Hunt cards do not query
  a SIEM or execute searches. ATT&CK mappings identify official references, not
  verified local detection coverage.
- Previous result revisions are retained in the database, but a dedicated
  revision comparison/history interface is a separate improvement.
- Team assessment activity appears in task history and AI statistics. Separate
  feature-health cards and independently configurable hunt-provider routing are
  possible later refinements.
- Qualification uses synthetic provider responses. Model-specific relevance and
  hypothesis quality should be evaluated on representative authorized material
  before broad operational use.

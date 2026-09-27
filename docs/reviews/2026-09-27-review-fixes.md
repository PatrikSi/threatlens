# Comprehensive review corrections — 2026-09-27

This follow-up addresses RV01–RV15 from the review of `799e9ed`. Changes preserve
the existing application API and receiver acceptance protocol while correcting
policy freshness, durable recovery, evidence provenance and editor lifecycle.

## Changes

| Findings | Correction |
|---|---|
| RV01, RV02 | The OpenSearch receiver checks the fresh pending-policy head before each vendor advance. Callback failures remain visible without starving eligible vendor work. Pending policy work deliberately still defers vendor advances globally. |
| RV03 | Database-generated SHA-256 identity columns bound unique indexes while preserving exact GUIDs, deduplication keys and canonical URLs. Lookup verifies both digest and original identity. |
| RV04, RV05 | Composed forms share one route blocker. Schedule drafts survive reporting tab switches and require deliberate discard. |
| RV06 | Continuation can reuse completed sections after exact prior-run receipts prove interrupted calls were not sent; unresolved calls remain blocked. At the authorization ceiling, a proven-unsent section can retry within remaining tokens without granting more budget. |
| RV07 | Context fitting drops complete passages and publishes the selection actually sent to the model. |
| RV08 | Visible fenced/indented report blocks need explicit adjacent source attribution. Literal citation text inside a code block is not a source link. |
| RV09 | Attempt scheduling is separate from committed reconciliation freshness. Failures and overdue subscriptions remain visible. |
| RV10–RV12 | Destination reload retrieves a fresh revision; late reporting completions cannot hijack another view; unavailable publication artifacts do not permanently disable team controls. |
| RV13 | Due automation work has a matching partial index, including prepared-query compatibility, and a retained-history query-plan regression. |
| RV14 | Receiver findings are validated before storage. Exact invalid callbacks rejected with HTTP 422 have a controlled, audited repair path; ambiguous sends cannot be rewritten. |
| RV15 | Provider qualification requires its advertised numeric table and cited data row. |

Backend lint now covers tests and scripts as well as application modules. The
fixture-import errors found by the review are corrected. Delegated MCP
authorization is documented in [ADR 0007](../architecture/0007-delegated-mcp-authorization.md).
Team administration now displays group names and copyable identifiers, with
explicit provider and handling-label controls. Statistics provides an explicit
action to restore all accessible feeds. Form tests exercise real data routers
and wait for usable controls rather than assuming a fixed query delay.
The populated-upgrade fixture also now derives the sole Alembic head instead
of incorrectly expecting migration `0105` after a successful current upgrade.
Final regression checks also exposed a small-context report-planning boundary:
optional metadata could fit while leaving almost no room for evidence. Planning
now reserves space for representative findings before compacting optional
metadata, preserves exact quotations, and discloses metric compaction. The shared
citation corpus covers both rejected uncited code and accepted source captions.

## Upgrade and rollback

Run migrations through `0124_reconciliation_progress` before starting the updated
API and workers. Migration `0123_item_identity_indexes` populates two stored
digest columns in one `items` table rewrite and rebuilds its identity indexes.
Allow maintenance time and free storage for the table/index rewrite; measure the
duration on a restored copy of a large installation before its production
upgrade. No administrative privilege is added to normal application operations.

Legacy item IDs and raw publisher identities stay unchanged. Older direct
writers are protected by database-generated digests. Downgrade refuses when
retained long identities cannot safely fit the previous raw B-tree indexes; it
does not truncate evidence or delete articles. Use a compatible application
version or deliberately resolve those records under the retention policy.

Migration `0124` treats historical reconciliation timestamps as attempts, not
proof of success. Publication freshness can initially appear overdue until a
successful sweep occurs. Receiver SQLite upgrades are additive; retain its
database and the remote action ledger. The protocol still cannot atomically
couple a later policy change with an external vendor launch.

## Verification

Tests use disposable databases, synthetic provider responses and isolated vendor
fixtures. They do not establish production capacity or analyst approval of AI
interpretations.

- [OpenSearch 3.8.0 contract](capacity/2026-09-27-review-fixes-opensearch.json):
  two actions produced exactly two launches, search identity survived lost
  acceptance responses, explicit binding recovered the original search, and
  withdrawal preserved completed history without returning source documents.
- [90-second sustained local workload](capacity/2026-09-27-review-fixes-sustained.json):
  concurrent ingestion, processing repair, AI, governance and disjoint exports
  passed every configured budget. The run recorded 72 ingested articles,
  approximately 72 MiB of process RSS growth, no task/sampler errors, and a
  maximum observed pending age of 5.2 seconds. This capped thread-worker fixture
  does not qualify production ingress, prefork topology or longer sustained load.
- Backend: the full regression run passed 4,349 tests, skipped five optional
  SDK/capacity cases, and exposed the two reporting failures described above.
  After those corrections, all 315 affected reporting tests passed. The final
  continuation changes passed 46 affected tests, and 13 database identity,
  runtime-privilege and query-plan checks passed separately.
- Final combined line/branch coverage is 86.85% overall and 87.08% for reporting;
  every critical-module floor passed. The two modules refined after the full
  run use their final measured reruns in place of the earlier measurements,
  without retaining stale line or branch data. This is full-run plus targeted
  verification, not a second full-suite run after the final refinements.
- Frontend: 158 test files and 1,277 tests passed. The browser interaction matrix
  passed 102 workflows across Chromium, Firefox and WebKit, including draft
  navigation, schedule tabs, keyboard interaction and automated accessibility
  checks. The new team workflow's three corrected browser assertions were rerun
  separately after the other 99 passed. All 31 shared frontend citation/rendering
  checks also passed after the final corpus correction.
- Real-server Chromium: 19 distinct workflows passed against disposable API,
  PostgreSQL and Redis services. The default run passed 17 and intentionally
  skipped two AI-enabled workflows; both passed in the isolated AI-enabled
  follow-up. These runs cover authentication, access loss, transient session
  verification, previews, exports, editorial/team workflows and statistics.
- Populated PostgreSQL migration round trip: legacy `0042` and enterprise `0100`
  fixtures upgraded to `0124`, retained-data verification and downgrade guards
  passed, `alembic check` found no schema drift, and downgrade to base followed
  by a clean upgrade to head succeeded. Limited runtime credentials also passed
  long-identity insertion/update and uniqueness checks without DDL privileges.
- Static and artifact checks: expanded Python lint, Python compilation,
  frontend lint/type checking, the production build and bundle smoke test,
  the 987-file source-size gate, generated API/preview fixture checks, changed
  documentation links and aggregate whitespace checks passed.

Independent reviewers checked receiver policy/callback isolation, continuation
receipt ownership, draft-dialog handoff, publication recovery and database
invariants. A final 112-plan report-budget matrix preserved exact quotations and
serialized input bounds; 16 invalid budgets were rejected cleanly.

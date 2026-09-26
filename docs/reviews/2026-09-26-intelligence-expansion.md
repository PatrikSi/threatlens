# Intelligence expansion and qualification — 2026-09-26

This change implements the nine selected workstreams. Existing raw exports,
legacy AI configuration, event-wide webhook conditions and notification templates
retain their prior contracts. New API routes have explicit permission and data
policy classifications. Migrations advance the schema from 0110 to 0115; upgrade
API, workers, scheduler and frontend together.

## Implemented behavior

| Area | Implementation | Boundaries and remaining qualification |
|---|---|---|
| AI quality evaluation | Versioned adversarial corpus, exact dataset hashes, model/prompt comparisons, fixture precision/recall, analyst claim judgments, usefulness, latency and disclosed cost. | Seed cases are AI-authored and **pending analyst approval**. No live model comparison or accuracy claim is represented as completed. The reviewed-corpus gate rejects the seed set. |
| Long-article extraction | Durable section checkpoints, exact source and rendered-plan fingerprints, deduplication, per-section evidence offsets and visible completed/uncovered coverage. | Eight sections of up to 8,000 normalized characters; conservative 64,000 aggregate token budget; bounded merged output. Summary/relevance still use the first section. Ambiguous provider outcomes require reconciliation. |
| External SIEM execution | Stable action/execution IDs, authenticated sequenced callbacks, monotonic states, durable callback deduplication, findings attached to existing investigations and execution UI. | The included signed receiver demonstrates the protocol. Actual SIEM job lookup, launch and polling require a vendor adapter. Unknown status never authorizes another launch. |
| Retractions and policy changes | Durable withdrawal/replacement control stream, ordered receiver ACKs, fair bounded reconciliation, owner/team/handling-policy checks and restore quarantine. | Receivers must poll and apply the stream. Completed hunts remain historical. Destinations remain personally owned; offboarding must preserve a credential long enough to reconcile. Receipt history currently needs explicit retention planning. |
| Same-indicator conditions | Explicit any/all indicator groups, conjunctive predicates on the same nonexcluded indicator and bounded per-indicator preview diagnostics. | Existing event-wide condition semantics stay unchanged. Incomplete or malformed inventories produce unknown outcomes rather than unsafe matches. |
| Reviewed publications | Team-approved STIX/MISP snapshots, exact previews, historical access labels, evidence/expiry/provenance, stable IDs, monotonic withdrawals and manual withdrawal. | At most 100 articles, 250 reviewed observations and 1 MiB snapshot; consumers reimport updates. Losing source access or retention can withhold the artifact; consumers must stop using an unverifiable approval. |
| AI routing and shared quotas | Independent team-assessment provider assignment, inheritance for older configurations, shared account concurrency/token accounting and durable per-team admission. | Queued work keeps its accepted provider selection. Limits apply to configured account groups; upstream capacity and prices remain provider/operator supplied. |
| Team hunt queue | URL-backed team filters, bounded keyset pagination, pending/stale/accepted/rejected suggestions, evidence age, persistent claims, reviewer details and investigation outcomes. | Claims coordinate review; investigation assignment and execution reuse the existing investigation workflow. Empty filtered pages can legitimately have a continuation cursor. |
| Operational qualification | Independent host monitoring, per-replica memory pressure, storage objectives, incident/heartbeat delivery, signed backup/key/recovery evidence, disposable ingress/worker workload and source-loss reconstruction tooling. | Local simulations cannot satisfy production recovery objectives. Actual hardware, off-host storage, key custody, ingress configuration and sustained production workload still need operator-run qualification. |

## Independent review corrections

Reviewers checked execution ownership, authorization changes, restore behavior,
AI resume invariants, publication interoperability and monitoring failure modes.
Corrections included:

- Rejecting a same-task extraction resume after any rendered plan change, without resetting its spent budget or repeating provider I/O.
- Withdrawing previously sent intelligence after durable owner access changes and quarantining restored external state without erasing completed history.
- Preserving MISP update timestamps, deduplicating equal attributes while retaining their evidence, and retaining stable revoked identities.
- Holding publication authorization fences through bounded downloads; retaining historical and newly restrictive audit labels.
- Removing the manual-withdrawal conflict loop and hiding approved previews after confirmed access loss.
- Keeping reviewer identity, timestamps and annotations outside the hunt's semantic approval fingerprint, while preserving invalidation for changed hypotheses, evidence, context and source revisions.
- Batching execution authority checks for static owners while rechecking expiring OIDC grants and live team access. Reconciliation of 100 receipts fell from 1,803 to 715 SQL statements; listing 25 or 100 receipts takes 34 statements. Source checks and savepoints still scale with the bounded reconciliation batch.
- Distinguishing absent measurements from zero, detecting pressure on an individual replica, and preventing old/local recovery reports from renewing production recovery objectives.

## Validation

Tests use disposable resources; the existing local application stack was not
redeployed by this implementation. Validation covers:

- Full backend regression: 4,037 passed and five skipped in the initial run. Its five failures were corrected: an audit-label fixture assumption, the hunt annotation fingerprint bug, and three migration-test savepoint fixtures. All 73 tests across the affected publication, hunt, execution and migration files then passed against the final commits. Final combined line/branch coverage is **86.75%**; reporting coverage is **86.74%**, and every critical-module coverage threshold passed.
- Frontend: 1,243 tests across 150 files; TypeScript, ESLint, production build and distribution checks passed.
- Browser workflows: all 84 cases passed across Chromium, Firefox and WebKit, including publication approval, retry identity, withdrawal and failed-preview recovery. One Firefox renderer crash passed on an isolated rerun. A further 21 real-server cases passed across Chromium and Firefox, covering authentication, teams, AI routing and statistics.
- Database migration qualification: seven persistent PostgreSQL cases passed, including populated 0110 → 0115 upgrades, safe downgrade/upgrade, retained-state rollback guards, model/schema comparison and `alembic check`.
- Execution reconciliation: 25 focused PostgreSQL cases passed across lifecycle, query scaling, expiring authorization, owner separation and migrations.
- Publications and hunts: 48 focused cases passed across approval, withdrawal, audit-label retention, event eligibility, review annotations, claims and assessment lifecycle.
- Independent operations monitor: 19 unit tests passed; the opt-in destructive recovery test was run separately in disposable resources.
- Python lint and compilation, the 921-file source-size gate, generated API contract and documentation link checks passed.

The [five-minute local workload record](capacity/2026-09-26-local-topology.json)
contains 6,259 requests through nginx with no HTTP errors and 89.2 ms p95 latency.
Five rounds processed 200 articles and verified ten exports from disjoint source
sets. Worst backlog recovery was 9.37 seconds; peak aggregate process RSS was
2.02 GB, counting shared pages separately for each process. The artifact records
source and nginx hashes, image identities and the exact workload limits.

The [source-loss reconstruction record](capacity/2026-09-26-host-loss.json)
passed in 128.5 seconds after deleting the disposable original data volumes and
credentials. A separate test backup copy and escrow key restored fresh services;
decryption, runtime privilege restrictions and restored-feed quarantine were
verified. Both artifacts explicitly set `production_qualified: false`.

No paid AI requests, real SIEM launches or external alert deliveries were used.
Production acceptance still requires analyst review of the evaluation corpus,
model comparisons, a configured SIEM adapter, independent backup/key custody,
and sustained qualification through the intended host and ingress topology.
The development host had approximately 340 MiB of free filesystem space at the
end of validation. Resolve that storage pressure before larger workloads; these
local measurements do not demonstrate spare production capacity.

## Operational handover

- [AI quality and coverage](../pages/ai-quality-and-coverage.md)
- [Execution callbacks and receiver protocol](../pages/automation-execution.md)
- [Same-indicator conditions](../pages/indicator-conditions.md)
- [Reviewed publications](../pages/reviewed-publications.md)
- [Team workflows](../pages/teams.md)
- [Provider routing and quotas](../pages/ai.md)
- [Operational qualification](../reference/operational-qualification.md)

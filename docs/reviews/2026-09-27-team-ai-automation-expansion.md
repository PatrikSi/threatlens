# Team AI, automation and MCP expansion — 2026-09-27

This change implements the ten requested workstreams. Migrations advance from
0115 to 0122. Upgrade API, workers, scheduler and frontend together. Existing
personal integrations, ordinary API credentials and installation-wide AI routing
remain supported. OAuth is opt-in; the new SIEM adapter is OpenSearch.

## Implementation and boundaries

| Area | Delivered behavior | Operational boundary |
|---|---|---|
| Receiver retry fairness | Durable independent retry times, exponential backoff and fair callback/policy-acknowledgement selection in the SQLite receiver. | A permanently failing first batch no longer prevents later jobs from running. Preserve the receiver database across restarts. |
| Team integrations | Manager-controlled destinations, explicit custody transfer, scoped receiver credentials and shared execution history. User removal preserves team history, signing credentials and withdrawals. | Team ownership does not grant another person's clearance. New deliveries require a current eligible custodian; accepted work retains its original authority. Initial transfer is bounded to 5,000 retained receipts. |
| Long-article analysis | Explicit idempotent continuation, durable section checkpoints, whole-covered-article synthesis, and relevant primary passages selected across sections for hunts. | Each continuation adds at most eight sections/64,000 estimated tokens; cumulative ceilings are 32 sections/256,000 tokens. Coverage and synthesis fallback remain visible. Source, prompt or provider changes stop incompatible continuation. |
| AI quality and qualification | Exact-content dataset approvals, individually identified claim judgments, configurable quality/cost/latency gates, and asynchronous extraction/hunt/report provider contract probes. | The seed corpus still requires real analyst review. Contract qualification is not semantic approval. Ambiguous paid calls are never replayed automatically. |
| OpenSearch connector | Approved literal indicator queries, stable remote action ledger, launch-or-lookup, status polling, bounded findings and withdrawal cancellation. | A lost vendor response can leave the async-search ID unknown. The launch tombstone prevents duplicates; an operator must verify and bind the original search before recovery can continue. |
| Publication distribution | Team consumer registration, idempotent subscription, resumable sequence feed, per-change acknowledgements and opaque withdrawals after source access loss. Retirement drains withdrawal obligations before archive. | Feed tokens cannot download evidence. Ordinary downloads still require current authorization. Replay gaps require an explicit discard-and-reset handshake. Consumer/subscription/change limits and backpressure are disclosed. |
| Automation health and retention | Execution update age, unknown outcomes, unacknowledged withdrawal age and publication reconciliation lag. Bounded cold-receipt archival and acknowledged-feed pruning. | Unknown and unacknowledged work remains protected. Archived receipts retain evidence and deduplication identities; archival is not physical deletion of that history. A timeout never permits a replacement launch. |
| Team AI governance and capacity | Approved team provider overrides and handling rules, shared account minute/hour windows, team allocations, fair admission and utilization/deferral diagnostics. | Queued tasks keep their accepted provider. Current destination and handling policy are rechecked before each send. Account groups and capacity limits must match the actual upstream account. |
| Hunt review workflow | Oldest-first and overdue views, priority, review deadlines, saved team filters, versioned claims and deduplicated in-app reminders. | Review claims remain separate from execution assignment. Investigations continue to own execution and outcomes. |
| MCP access | Nine read-only tools, exact normalized IoC and ATT&CK lookup, current assessments/hunt queues/publications, and bounded evidence pagination pinned to source revisions. Optional pre-registered OAuth clients use browser consent and S256 PKCE. | Delegated tokens last 15 minutes, are restricted to the MCP resource and cannot call ordinary APIs. Existing scoped bearer credentials still work. No MCP tool changes records or launches hunts. |

## Independent review corrections

Three independent workstreams reviewed authorization, worker ownership,
failure recovery, input limits, migrations and asynchronous UI behavior. Review
and running-browser tests found and corrected:

- Callback starvation and independent acknowledgement retry starvation.
- Offboarding cascades, receiver credential expiry after lock waits, and
  personally owned signing material accidentally remaining a team dependency.
- Publication registration retries, missing original-custodian clearance checks,
  replay retention starvation and retained-consumer capacity recovery.
- Unbounded section-summary aggregation and malformed evaluation input shapes.
- Continuation results reaching a changed source revision or exhausted ceiling.
- Consent drafts surviving changed OAuth requests and duplicate query parameters.
- Publication manager cache remaining stale after closing during registration,
  stale selections, and credentials remaining visible after confirmed access loss.
- Missing confirmation and inaccessible error placement for consumer retirement,
  revocation and archival.
- OAuth administrative mutation authorization after lock waits and retention of
  expired short-lived grants. Cleanup is bounded and preserves audit records.
- Ambiguous authentication headers, unsafe persisted display text and malformed
  callback URLs.
- Team service exceptions escaping the MCP error boundary, broken team links and
  malformed signed evidence positions.
- HTTP-layer dependencies in OAuth services; shared credential verification now
  belongs below both ordinary token and delegated authorization routes.
- Qualification jobs missing from worker inspection and stale-run discovery.
  Durable qualification recovery now enters through the scheduler's actual
  reconciliation path, while inline connection diagnostics retain their behavior.
- Restored machine credentials and acknowledgement IDs surviving backup rollback.
  Restore quarantine revokes credentials, retires consumers, withdraws restored
  publications and replaces acknowledgement identities without changing stable
  hunt action IDs. Retired consumers must drain specific withdrawal receipts.

## Validation

All execution tests use disposable resources or synthetic fixtures. No paid AI
request or production SIEM hunt was sent; the existing application stack was not
redeployed.

- Complete backend regression: 4,266 tests passed, five skipped, in 20 minutes
  54 seconds. Overall line/branch coverage was 86.56%; reporting coverage was
  86.74%. Every existing critical-module coverage floor passed.
- The final qualification-recovery changes passed 110 affected workflow,
  cancellation, continuation and operations tests, including paused consumers,
  active/reserved/scheduled inspection and ambiguous paid calls. Coverage for the
  four application modules changed during final review was replaced with their
  post-fix test measurements; the conservative combined result remained 86.45%
  overall with every coverage gate passing.
- Final restore corrections passed 56 additional/affected backend cases and
  14 recovery checks. These execute the packaged preflight, quarantine and verify
  SQL against migrated PostgreSQL, including repeated quarantine, STIX/MISP
  withdrawal formatting, invalidated credentials/acknowledgements, numeric
  generation rollback, and deliberate rotation/drainage/archive. Another 26
  receiver tests and a real-database orphan-destination drainage case passed.
- Complete web regression: 1,262 tests across 157 files passed. Added lifecycle
  cases were checked separately after browser review. TypeScript, ESLint,
  production build and distribution rendering passed.
- Full browser regression: 99 cases passed across Chromium, Firefox and WebKit,
  including 15 new consent/consumer lifecycle cases. Existing session isolation,
  nested dialogs, reporting, AI settings and accessible workflows also passed.
  These browser cases use intercepted API fixtures; database authorization is
  tested separately with PostgreSQL.
- A disposable nginx/Uvicorn stack passed real cookie login, CSRF-protected
  consent, PKCE exchange, replay rejection, MCP-only token audience, revocation
  and discovery. The official MCP SDK 2.2.0 completed discovery/search/evidence
  through the proxy in both 2026-07-28 and legacy 2025-11-25 modes.
  The isolated SDK suite also passed 119 protocol and two HTTP tests.
- Migration qualification: 22 cases passed, including populated 0111→0122
  upgrade/downgrade round trips, retained-state rollback guards and complete
  SQLAlchemy/Alembic metadata comparison.
- OpenSearch 3.8.0 qualification exercised actual launch/poll/findings, completed
  withdrawal and lost-response recovery. Two distinct actions produced exactly
  two launches. See the [measurement artifact](capacity/2026-09-27-opensearch-connector.json).
- Bootstrap and Kubernetes environment export: 15 and eight tests passed.
- Isolated capacity smoke: eight articles recovered in 8.86 seconds; export p95
  was 709 ms, AI p95 601 ms and governance p95 46 ms. RSS grew 40.7 MiB, with no
  budget violations or task/sampler errors. This is a small synthetic workload,
  not a sustained deployment qualification.
- The [worker recovery probe](capacity/2026-09-27-worker-recovery.json) passed
  against a clean source revision: an AOF Redis crash retained the accepted
  message, and a prefork child crash caused one redelivery and completed pipeline
  repair in 7.38 seconds. No task errors or budget violations were recorded.
- Compilation, Ruff, ESLint, the 984-file source-size gate, generated API schema,
  documentation links and CI workflow lint passed. A bounded OpenSearch contract
  job now exercises the vendor API in CI.

## Deployment acceptance still required

1. Have analysts review the corpus and exact model output claims; retain a passing
   promotion-gate artifact for the chosen provider/prompt revision. The repository
   intentionally does not claim those human approvals exist.
2. Qualify the OpenSearch connector against the intended TLS trust, least-privilege
   roles, index mappings, handling labels, volumes and operational recovery. Back
   up both its remote action ledger and the receiver database. The local fixture
   validates vendor behavior, not production capacity.
3. Choose approved team providers and shared account allocations. Run sustained
   mixed workloads through the target ingress and worker topology; local tests
   cannot establish deployment-specific latency, memory or recovery objectives.
4. Enable OAuth only with the correct public origin and explicitly registered
   client callbacks. Test the actual MCP client. Pre-registration is supported;
   dynamic client registration and refresh tokens are intentionally absent.
5. Configure consumer polling and acknowledgements, monitor lag, and rehearse
   replay-gap recovery. Evidence access loss does not remove withdrawal duties.
   After restoring a backup, rotate receiver credentials, discard queued
   acknowledgements and reset local cursors from current server status. Reconcile
   external records created after the backup using the receiver's own durable
   ledger; the restored database cannot enumerate records it never captured.

## Guides

- [Team integrations](../pages/team-integrations.md)
- [OpenSearch configuration and recovery](../pages/opensearch-connector.md)
- [AI quality, qualification and continuation](../pages/ai-quality-and-coverage.md)
- [Reviewed publication distribution](../pages/reviewed-publications.md)
- [Team workflows](../pages/teams.md)
- [AI governance and quotas](../pages/ai.md)
- [MCP tools and delegated authorization](../pages/mcp.md)
- [Deployment qualification](../reference/operational-qualification.md)

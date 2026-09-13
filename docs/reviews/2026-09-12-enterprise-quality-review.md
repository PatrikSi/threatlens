# Code review and enterprise workflow implementation — 2026-09-12

This review covered the API and authorization boundaries, ingestion and recovery,
report and export lifecycles, analytics, frontend cache/editor behavior, logging,
deployment, migrations and test contracts. Three independent reviewers worked
alongside implementation, including adversarial PostgreSQL lock tests and
disposable application/browser checks. Changes are committed on `dev` as
`Patrik <patrik@local>`.

## Implemented features

| Area | Result | Contract |
| --- | --- | --- |
| Statistics | Ingestion and AI analytics share Statistics. AI panels include provider/model attribution, feature outcomes, token completeness, successful latency percentiles/distribution, deadlines, timeouts, truncation, budget failures, retry receipts and backlog age. | Permission-aware SQL aggregation; ingestion and AI retain independent access controls. Unknown token usage is disclosed rather than counted as zero. |
| Named teams | Group-backed membership and managers, team configuration, shared views and investigations. | Team membership supplements feature permissions and evidence policy. No administrator bypass into team content. Shared ownership survives creator deletion. |
| Shared triage | Team watchlists, claim/unclaim, manager assignment, deadlines, escalation markers/activity and URL-preserved queue context. | Current membership and recipient eligibility, immutable ownership, optimistic versions and bounded dispatch. Escalation is visible in the application; it does not send external notifications. |
| Report publication | Draft editing, submission, approval, return to draft and publication; library status/filtering and review notes. | Approval pins exact content and evidence. Edits invalidate approval. Only publication emits delivery for review-governed reports. Legacy automatic publication is explicitly preserved. |
| Organization workspace policy | Role defaults or enforcement for start page and dashboard; saved-view templates; fixed navbar arrangements. | Policy does not grant feature access. Personal layouts remain separately stored; pending/dirty editors retain their baseline. Existing clients preserve omitted new policy fields. |

## Findings corrected

| Area | Confirmed issue | Correction and regression coverage |
| --- | --- | --- |
| Authorization and cache lifecycle | Background access denial could leave cached investigation, alert, report or AI data visible. | Shared terminal-access filtering and real QueryClient transition tests; temporary outages preserve work. |
| Team concurrency | An OIDC membership could expire while an occurrence or watchlist write waited for a row lock. | Fresh membership checks after lock acquisition; three real PostgreSQL contention regressions. |
| Report credentials | Editorial writes needed the same current caller credential fencing as protected delivery. | Account/token/session checks before report locking and again before commit; revoked/expired credential tests. |
| Draft conflicts | Saved-view refreshes could associate newer versions with older drafts. | Submitted baselines and capabilities remain stable through refresh, edit and cancellation; explicit conflict recovery. |
| Input and ingestion | Malformed feed metadata, unsuitable HTTP validators and unsafe watchlist text could fail persistence or request handling. | Storage-safe input checks, bounded metadata fields, safe optional conditional headers and actionable validation errors; valid Unicode and existing text spacing remain supported. |
| Dispatch resilience | Paused consumers could accumulate repeated export dispatches and queue canaries. | Durable export dispatch reservations and bounded canary queue admission; real Redis paused-consumer and recovery tests. |
| Analytics | Future timestamps could appear in historical statistics; feed series had no explicit default result bound. | Inclusive historical bounds, bounded series selection and visible scope disclosure. |
| Report concurrency and memory | Delete/retry could use stale report state; library paths selected more document content than necessary. | Lock/reload transitions and bounded SQL projections. |
| Logging | Authorization formats, structured context and URL query strings could expose secrets in logs. | Broader redaction, bounded safe context and sanitized proxy/application request diagnostics with live probes. |
| Database compatibility | The legacy alert compatibility trigger rejected deadline-only rule revisions. | Extended semantic change detection and immutable team ownership triggers, tested through repeated SLA-only edits. |
| Production packaging | The web-only Docker context omitted the shared citation fixture required by TypeScript. | One canonical corpus moved inside the web context; both backend/frontend consumers and clean Docker build tested. |
| Proxy limits | Valid large report drafts exceeded nginx's default upload limit. | A narrowly scoped 16 MiB draft route limit, preserving the ordinary API limit; real proxy regression script. |
| Modularity | Investigation membership and contract code increased the orchestration module's size. | Extracted typed contracts and membership operations while preserving public call sites and dependency gates. |
| Navigation and accessibility | Statistics permissions could remove its section navigation; team controls had labeling/landmark issues. | Independent section access, persistent navigation and accessible controls, with DOM and real-browser tests. |
| Session resilience | A failed verification attempt could block writes before the recovery dialog appeared during automatic retries. | The visible workspace boundary now subscribes to the same synchronous action guard; transition tests cover recovery and superseded requests. |
| Query caching | Team directories and triage selectors shared a cache key despite requesting different page sizes. | Page size is included in each key; a cached transition across a 51-team catalog checks that choices are not skipped. |
| Disaster recovery | Quarantine changed delivery intent inside an immutable published report's approval hash. | Preserve the original intent only for ready, published, hash-pinned reports while retaining all outbound quarantine fences; actual restore verifies unchanged pins and dead-lettered delivery. |
| Rollback safety | A downgrade could erase completed approval/publication history. | Refuse removal while editorial history remains; populated transactional guard tests verify schema and data stay intact after refusal. |

## Deployment and compatibility

The schema has one linear head through migrations `0101`–`0105`. The team alert
and report editorial changes require a coordinated API/worker rollout. In
particular, old alert workers are not team-aware. New review-governed reports use
`ai-reports-v3`; updated workers also drain the existing v2 queue. Existing
personal ownership, completed reports and legacy automatic schedule policy are
preserved. Downgrade protection must not be bypassed to discard newly owned
resources or editorial history. See [Teams](../pages/teams.md),
[Alerts](../pages/alerts.md#shared-team-triage),
[Reporting](../pages/reporting.md#failure-recovery), and
[workspace policies](../pages/settings.md).

Validation uses fresh databases, synthetic accounts, temporary build contexts and
isolated containers. It does not replace or rebuild the existing local deployment,
read its secrets, or modify user backups.

## Validation evidence

- Backend coverage run: **85.65% overall**, **86.61% reporting**; all critical
  module floors passed. Pinned route and queue expectations were updated for the
  new statistics endpoint and v3 report queue, then checked again. The subsequent
  full run passed **3,193 tests**, with two opt-in workload skips and one alert
  validation error-code compatibility failure. The existing `string_too_long`
  contract was restored; **all 32 related schema/API regressions passed**
  afterward. These figures combine the full suite and focused reruns.
- Frontend: **1,146 tests across 131 files**, application/browser TypeScript,
  full ESLint with zero warnings, production build and login bundle smoke passed
  on the final integrated frontend.
- Real-server browsers: **54 passed in 5.8 minutes**, with AI enabled, across
  Chromium, Firefox and WebKit. This includes real authentication/CSRF/OIDC,
  session outage recovery and account switching, exports, processing recovery,
  provider credential/routing changes, report editorial/publication, statistics
  access, team triage/access withdrawal and organization policy enforcement.
  The automated axe assertions in these workflows reported zero violations.
- Real PostgreSQL upgrade: populated `0042 → 0100 → 0105`, schema parity,
  transactional downgrade guards and `base → head` roundtrip. Repeated under the
  limited migration role; runtime DML works and DDL is denied.
- Clean tracked-source Docker builds for backend and web, migrated startup,
  34 authenticated API smoke assertions, runtime privilege checks and container
  resource/hardening inspection. Live proxy checks include healthy requests,
  oversized uploads, unavailable upstreams, request correlation and log privacy.
- Recovery: **87 tests passed**, four opt-in Docker cases skipped in the ordinary
  unit invocation; the populated Docker recovery workflow was run separately and
  passed in **254 seconds**. It covered backup verification, drill, injected
  restore failure/rollback and successful restore. All recovery shell scripts
  passed Bash syntax and ShellCheck; 14 focused hook checks passed.
- Locked Python dependency audit and frontend dependency audit found no known
  vulnerabilities. This is a dependency advisory check, not a claim that the
  entire system has no security defects.
- Source-size/complexity gates, Python compilation, TypeScript for application
  and browser tests, full ESLint, production bundle smoke, generated API contract
  and preview-policy fixture checks.
- [Capacity smoke measurements](capacity/2026-09-12-enterprise-smoke.json): no
  budget violations; queue recovery **5.64 seconds**, synchronous export P95
  **319 ms**, background export P95 **670 ms**, governance P95 **50 ms** and
  process RSS increase **37 MiB**. Two independent export source partitions
  completed during and after the mixed workload. This was a short synthetic
  smoke, not sustained or team-scale qualification.

Browser tests use real sessions, CSRF, PostgreSQL and Redis. Production handlers
perform export, alert-evaluation and publication transitions through explicitly
controlled synthetic fixtures. Browser cases do not assert live provider quality
or Celery delivery timing. Automated axe checks cover the tested pages; human
assistive-technology testing remains separate.

Run Docker recovery/build qualifications before the browser suite on this host.
Concurrent Docker bridge changes caused Chromium `ERR_NETWORK_CHANGED` failures
in earlier development-server runs. The final full browser run used a quiet host
network after the other disposable stacks were removed.

## Remaining improvement opportunities

| Priority | Area | Practical next improvement |
| --- | --- | --- |
| P2 | Report governance | Configurable independent reviewer/quorum and reviewer assignment. Self-review currently remains explicit and audited for small installations. |
| P2 | Team notifications | Team-managed notification destinations, escalation policies and on-call routing, with independent destination authorization and delivery audit. Current escalation is an application marker/activity. |
| P2 | Capacity | Sustained mixed workloads and fairness measurements on the intended deployment hardware, especially many active teams and large independent exports. Smoke tests are not a sustained capacity qualification. |
| P2 | Accessibility | Human keyboard/screen-reader validation and tagged PDF output. Automated axe and three-browser checks do not establish assistive-technology usability. |
| P2 | Team governance | Team retention policy, explicit archival/export workflow and ownership-transfer tooling. Current team resources have stable ownership; groups control access. |
| P3 | Analytics | Provider billing reconciliation and shared-account spend limits. Current usage reports disclose missing measurements and avoid inventing currency costs. |
| P3 | Scale | Search/keyset paging for growing team/member directories and larger shared watchlist catalogs. Current endpoints have explicit page/response limits. |
| P3 | Shared infrastructure | Move generally applicable credential-bound authorization helpers out of export-specific module names incrementally, retaining the same lock-order and revocation tests. |
| P3 | Policy flexibility | Group/team-specific workspace defaults and a separate team analytics permission. Current arrangement policy is per built-in role, and AI usage remains administrator-only. |

Team workspaces are not full tenant isolation for every feed, integration or AI
provider. Citation and revision validation establish provenance and approval
integrity; analysts still need to judge source reliability and factual claims.

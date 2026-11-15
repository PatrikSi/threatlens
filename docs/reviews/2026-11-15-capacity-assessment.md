# Capacity assessment — 2026-11-15

This is a source review of one ThreatLens installation, with independent review
of 26 pinned source/configuration documents at `bc9c7b9f` plus the pinned
working-tree snapshot. The backend topology
has not changed since that review. The browser retention correction has separate
[design and validation](../architecture/0008-browser-read-authority-retention.md).
No current-revision performance or recovery campaign was run. Historical results
are evidence about their recorded revisions and environments.

## Workload model and evidence

The relevant growth dimensions are retained articles/text, feed arrival rate and
publisher latency, source skew, simultaneous exports and optional AI work,
history/artifact retention, and distinct resources visited in one browser session.
Current production rates, freshness/p95 objectives, representative retention,
hardware/storage budget and session duration are unknown.

The [documented finite profiles](../reference/capacity-baseline.md) cover 30/500/
2,000 retained articles and 8/60/200 new articles. One feed has three times its
peers' items; all sources share a local domain. Bodies are repetitive synthetic
text and the provider is deterministic. Sustained workloads use paced closed-loop
lanes; they do not establish an open-loop arrival-rate limit.

[Provisional budgets](capacity/budgets.json), including baseline export p95
10 seconds and backlog recovery 120 seconds, are regression guardrails. They are
not an SLA, deployment sizing result or supported maximum. The ordinary suite's
capacity/recovery skips do not satisfy these opt-in workloads.

## Verified containment and likely bottlenecks

| Surface | Current source/configuration bound | Growth consequence |
| --- | --- | --- |
| Container memory | Default steady-state ceilings total 10.75 GiB; migration adds 512 MiB. Individual services have memory/swap, CPU and PID ceilings. | Containment does not promise throughput. Backend/export tmpfs also consumes container memory. |
| Database | Default API pool 8+2; ordinary process pool 2+0; export pool 4. Conservative documented topology inventory is 64 slots against PostgreSQL's default 100. | Recount total connections before increasing processes or replicas. Preserve administrative headroom. |
| Database waits | Pool checkout 10 seconds, lock waits 5 seconds and ordinary statements 30 seconds. | Saturation has bounded failure behavior, including sanitized `database_busy` 503 responses. |
| Workers | Four shared ingest/processing children, one export, one AI, one maintenance and four notification children; prefetch multiplier 1. | Isolation protects other work from expensive exports/providers. Extra children also increase memory and pool demand. |
| Network ingestion | Feed/article limits 2/4 million bytes; total deadlines 60/90 seconds; per-domain concurrency 2; bounded retries. | Slow hot domains can consume scarce worker and transaction slots. Capped feed bytes do not independently cap parser record count/CPU. |
| Dispatch/processing | Due-feed scan at most 5×500 IDs and 500 publications per cycle; processing admission 200, batch 50, per-feed 5. | Per-tick work is bounded; total backlog still depends on offered work and service rate. |
| Exports | 10,000 items, PDF 500; 250 million uncompressed bytes; bounded record batches; per-principal active/retained/reserved-byte quotas. | Durable admission rejects excess work. Disk/tmpfs and artifact concurrency can constrain the installation before logical quotas. |
| Optional AI | Separate admission pool, bounded DB-only reservations and configurable provider/token concurrency/quotas. | Real-provider latency, rate limits and spend remain external constraints. Unlimited settings are not capacity guarantees. |
| Retention | Lifecycle scan/transaction/child-row batches are bounded. | Corpus, indexes, history and encrypted artifacts still increase disk, I/O and maintenance demand. |

Shared PostgreSQL authorization fences and row locks are likely contention points
under concurrent exports, governance changes and providers. Article fetching can
retain an item transaction during bounded outbound work. Slow publishers can
therefore raise both queue age and database occupancy.

No global logical byte/count cap was established for the total Redis outage
backlog. Container limits bound the resource, while eventual recovery still
depends on arrival/drain rates and deployment persistence. This is an unvalidated
operating envelope, not a measured loss or a new claim of durable delivery.

## Browser retention correction

The original session map retained scopes for every distinct visited resource
after query garbage collection. An actual Node GC probe reproduced that retention;
browser heap impact and a practical failure threshold were not measured.

The correction weakly retains exact scopes and keeps a conservative historical
summary of at most 64 KiB per client. Fresh unique epochs prevent stale cache-copy
aliasing. A known withdrawal still blocks list-only actions after detail-cache
collection. The summary can conservatively require opening details and completing
a fresh read before automatic actions or an original preview recover. Live query
memory and weak-key cleanup remain dependent on active state and GC timing.
See the [ADR](../architecture/0008-browser-read-authority-retention.md) and the
[review work log](2026-11-15-comprehensive-review.md) for actual validation.

## Operating signals and remaining validation

Observe queue oldest age and drain rate alongside queue length, database pool
timeouts/lock-waiting query age, Redis and container memory/OOM events, export
reservations/tmpfs, and disk/history growth. Source contains these operational
signals; installation-specific alert thresholds and response procedures still
need verification.

Before sizing, record hardware, cgroups, replica/process counts, representative
article sizes and source/provider distributions, retention and explicit latency/
freshness/error targets. Reuse compatible synthetic guardrails only in an
authorized isolated campaign, with enough successful samples, total service
memory and pool/backlog measurements. Preserve safe rejections and ambiguous
provider outcomes in the results.

Unmeasured areas include current HTTP p95/concurrency, sustained publisher skew,
real-provider latency/cost, long retention/index growth, multi-replica pool totals,
Redis backlog size versus memory, simultaneous tmpfs peaks, disk IOPS and browser
heap growth. No evidence here supports arbitrary worker multiplication, sharding
or an enterprise-scale capacity claim. The next executable capacity step is to
define that workload and target environment, then run its explicitly authorized
qualification campaign.

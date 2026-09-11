# Processing freshness and pressure history

System health now measures classification, automatic tagging and export queues
alongside report generation and delivery. These are database aggregates; article
bodies are not loaded for monitoring. The summary requires `read:operations`;
opening individual affected items additionally requires the processing
worklist's item access checks.

| Queue | Default freshness objective | Action on a breach |
| --- | ---: | --- |
| Classification | 600 seconds | Check processing consumers and dependency errors, then inspect selected incomplete items. |
| Automatic tagging | 900 seconds | Inspect rule failures and retry eligibility in Processing; address timeout or invalid rule causes before retrying. |
| Export generation waiting to start | 600 seconds | Check the dedicated export worker, admission pressure, and retained job errors. |

Configure these with `CLASSIFICATION_FRESHNESS_SECONDS`,
`TAGGING_FRESHNESS_SECONDS` and `EXPORT_FRESHNESS_SECONDS`. A breach is inclusive
at the threshold and creates an actionable System health issue. Expired active
leases are critical; pending age and failures needing attention are degraded.
These are operational warning objectives, not throughput guarantees.

Classification age begins when the current source version requires processing.
Tagging has its own pending timestamp. Refreshing an old article starts a new
obligation instead of inheriting the article's original ingestion age. Existing
obligations whose original start time was not recorded receive the migration
time as their baseline; older delay cannot be reconstructed accurately. Export
waiting age uses the accepted job's creation time.

Processing failures needing attention include cancelled work that exhausted its
automatic attempts for the item's current source version. Completed obligations
and failures for earlier source versions do not affect the current backlog.

Export failure counts retain all failed jobs for history. Health degrades for
technical failures completed within the last 15 minutes (inclusive); records
without a completion timestamp use their creation time. Authorization changes,
size limits, empty results, changed source snapshots and deleted owners are
expected terminal outcomes and do not degrade export health. Other or missing
error codes are conservatively treated as technical failures. A successful
replacement does not remove failure history; recent technical failures age out
of the health signal after 15 minutes. Queued-age breaches and expired running
leases affect health independently of failure history.

The five-minute health history retains per-stage pending/active/failure counts
and oldest pending age, plus a fixed set of runtime pressure measurements.
Older samples without these fields remain unknown. The existing bounded
retention and incident-preserving history selection apply to the new fields.

Runtime capacity includes PostgreSQL connections and lock waiters for the
current runtime database role, and the oldest observed wait/transaction age.
It does not require `pg_monitor` or cluster-wide superuser access. Current
memory usage, memory ceiling, OOM kill count and memory pressure come from the
collecting container's cgroup v2; process RSS comes from `/proc/self/statm`.
Memory scope is the collector, not the entire fleet. Unsupported/unavailable
cgroup metrics remain unknown. If container usage is unavailable, process RSS
alone can provide a usable sample; an unlimited or unavailable ceiling leaves
container utilization unknown. The component summary identifies these partial
memory modes. If both container usage and process RSS are unavailable, runtime
capacity is unknown even when database and deadline telemetry are available.
An observed pressure or deadline failure still degrades the component despite
missing telemetry. Container memory at or above 85% or a sampled database
lock wait of at least five seconds produces a pressure issue.

Database lock/statement/pool/deadline failures, outbound total-deadline failures,
and export transfer/generation deadlines have shared Redis counters. Labels are
fixed and contain no user, query, destination or item identifiers. Counters use
expiring minute buckets; the displayed value covers the current and previous
14 minute buckets. Do not sum overlapping history values. Each process admits
at most two background writes and one counter read, with no waiting queue.
Excess writes are dropped; reads return unknown when busy, failed, or unfinished
after 200 ms. DNS resolution runs inside those bounded daemon workers, outside
the application operation's deadline path. Forked workers reset inherited metric
clients and admission slots. Writes are best effort, so lost telemetry must not
be interpreted as proof that no error occurred. Any
observed deadline event creates a recent-pressure issue that ages out with the
window. OOM totals are informational since the collecting container started.

Use these measurements with the [runtime budgets](runtime-budgets.md),
[processing worklist](processing.md), and
[capacity release comparisons](../reference/capacity-baseline.md). Container
limits and database waits can explain regressions; they do not replace sustained
mixed-workload measurements on the intended deployment hardware.

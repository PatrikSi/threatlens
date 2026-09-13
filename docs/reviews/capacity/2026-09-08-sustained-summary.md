# Sustained workload on the shared lab host, 2026-09-08

Source: `3cbdf67b61052464ba47ae50cb05fe06fdc88d91`.
[Complete measurements](2026-09-08-sustained-600s.json).
[Reproduction and measurement limits](../../reference/capacity-baseline.md).

The 600-second paced workload passed all declared budgets. It ran on the
current shared Linux host (four logical AMD EPYC-Rome CPUs, about 15.6 GiB
RAM), with the application limited to one allowed CPU at nice 10. The owned
process-tree RSS guard was 1 GiB. Disposable PostgreSQL and Redis each had a
0.5 CPU quota and 512/128 MiB memory limit with no extra swap. No live
application data, credentials, containers, or stack were used. Other full
suites and builds were deferred during the measurement window; this was still
a shared host rather than a dedicated benchmark machine.

| Observation | Measured result |
| --- | ---: |
| Sustained duration | 600 s |
| New articles, all classified with IOC extraction complete | 480 across 60 feed batches |
| Export / governance / AI operations during the interval | 300 / 300 / 300 |
| Successful exports including final stable check | 299 |
| Safe export policy conflicts | 2 |
| Successful AI requests including final stable check | 301 |
| Successful export p95 | 410 ms |
| Governance p95 | 82 ms |
| Successful AI operation p95 | 253 ms |
| Python process peak RSS / increase | 282.3 / 35.7 MiB |
| Owned process-tree peak RSS, shared pages counted per process | 293.3 MiB |
| Peak queued plus unacknowledged messages | 24 |
| Peak sampled oldest pending-message age | 3.162 s |
| Peak sampled age of a query waiting for a lock | 173 ms |
| Observed DNS deadline elapsed, six 100 ms probes | 100–102 ms |
| Observed header-read deadline elapsed, six 100 ms probes | 101–102 ms |
| Unexpected task/sampler errors or budget violations | 0 |

The backlog was already drained when the paced load stopped; the final
completion check took about 4 ms. That number is not an outage recovery
measurement. The separate recovery profile tests broker restart and prefork
child termination. Service lanes used fixed two-second pacing, with governance
and AI start phases of 500 ms and 250 ms, respectively. This avoids artificial
lockstep while keeping the workload reproducible. Missing-IOC repair ran at an
explicitly shortened five-second interval. Retained synthetic catalog data was
200 articles of 8 KiB each. These counts, phases, limits, and maintenance
settings are encoded in the artifact's comparison identity.

This result establishes a reproducible bounded baseline at 48 new articles per
minute and about 0.5 operations per second in each service lane. It does not
establish saturation throughput, HTTP ingress capacity, production worker-pool
sizing, long-term memory stability, or capacity for larger histories and
arbitrary article distributions. The deterministic local AI endpoint models
network/request work, not inference compute. Future release comparisons must
use compatible version 2 measurement identities and report outcome counts as
well as latency; older burst artifacts are not interchangeable with this run.

The export lane measured the projection/artifact service. It did not exercise
the subsequently added asynchronous export-job admission, encrypted PostgreSQL
artifact chunks, or download route. Those paths need a separate workload
contract and baseline before making a capacity claim about them.

## Independent worker and broker recovery

The [recorded recovery run](2026-09-08-recovery.json) used clean source
`d2528be2714b9cefd069841c930af07d77c49a2e` on the same capped shared host.
Its separate prefork workload passed both failure budgets. An accepted Redis
message survived an actual SIGKILL and restart of the fixture-owned broker
with AOF and appendfsync always; restart took 634 ms. A worker child was then
killed while it held the article database lock. The same task ID was
redelivered to a replacement child, durable repair was dispatched, and all
three unique articles reached current classification and completed IOC states
within 6.473 seconds. The producer remained alive throughout and held no
unused result-backend subscriptions.

The owned process-tree RSS peak was 608.8 MiB across three processes, including
shared pages once per process. The runner took 31.1 seconds including fixture
startup and cleanup; the fault measurement itself took 10.3 seconds. There
were no unexpected task/sampler errors or budget violations. This proves the
recorded process-crash and durable-broker-restart scenarios under this small
workload. It does not cover every broker persistence mode, host power loss,
network partition, or whole-worker-fleet outage. Recovery and sustained
profiles have distinct workload contracts and must not be compared as one
performance series.

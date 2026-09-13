# Hardening capacity qualification — 2026-09-11

All four profiles passed against immutable application/test revision `6823cd6`.
Artifacts record `source_dirty=false`, no task or sampler failures, and no
configured budget violations. This is a new local baseline for the complete-stage
workload contract; older IOC-only measurements are incompatible.

| Profile | Completed work | Recovery measurement | Process RSS increase |
| --- | --- | --- | ---: |
| [Smoke](2026-09-11-hardening-smoke.json) | Eight fully processed articles; four export/governance/AI cycles each; paired durable exports | 4.92 seconds, including consumer startup | 33.3 MiB |
| [Sustained, 600 seconds](2026-09-11-hardening-sustained-600s.json) | 480 fully processed articles; 300 export/governance/AI cycles each; all four paired durable exports ready | 11.7 ms drain after load; peak queued-message age 2.78 seconds | 65.5 MiB |
| [Large](2026-09-11-hardening-large.json) | 200 fully processed articles; 40 export/governance/AI cycles each; two disjoint 1,000-article exports with 64 KiB bodies | 71.53 seconds for the burst and consumer startup | 130.6 MiB |
| [Crash recovery](2026-09-11-hardening-recovery.json) | Accepted Redis message preserved; killed prefork child replaced; all three articles complete without duplicate articles or producer restart | Redis restart 0.42 seconds; worker recovery 6.01 seconds | See owned-process-tree measurement in artifact |

Sustained successful-export p95 was 429 ms, successful AI p95 264 ms, and governance
p95 92 ms. Of 301 export attempts including the final settled check, 293 succeeded
and eight were safely rejected after a policy change. All 301 AI checks succeeded.
Maximum sampled age of a query observed waiting on a database lock was 857 ms.
The full workload, including setup within the measurement and shutdown, took
604.35 seconds.

In the large profile, policy changes safely rejected both initial paired jobs.
After governance settled, both concurrent generators published 1,000 distinct
source items each, producing 66,438,780-byte artifacts in about 14.43 seconds per
generator. These two samples establish correctness and bounded completion, not
an asynchronous-export latency percentile. The separate higher-sample successful
export lane had a 2.60-second p95. Peak queued-message age was 31.43 seconds;
maximum sampled lock-waiting query age was 2.16 seconds.

## Conditions and interpretation

The available host had four virtual CPUs and approximately 16 GiB RAM. The mixed
application simulation used one allowed CPU, nice 10, a 1 GiB owned-process RSS
watchdog and an explicit 16-connection pool with no overflow. Its PostgreSQL and
Redis containers each had a 0.5-CPU quota, with 512 MiB and 128 MiB memory limits.
The profiles ran sequentially; other backend qualification ran on CPUs 2–3 with
separate disposable services. This shared-host context can affect timings and is
not a quiet, intended-hardware release comparison.

The mixed profiles dispatch all-stage repair every five seconds with a two-second
missing-article grace period. Crash recovery uses zero article grace. Production
retains its 30-second dispatch interval and 300-second article grace. Completion
requires nonblank successfully fetched content, current classification, completed
IOC extraction, no pending tagging and an empty broker. The result identity
records these contracts and settings. Burst/startup recovery and post-load drain
measure different intervals and must not be compared as the same latency.

The separate [runtime isolation artifact](2026-09-11-hardening-runtime-isolation.json)
records the actual eleven-service Compose constraints at `8f1d53e`, successful
CPU-pressure readiness checks, an isolated OOM probe and export/Beat restart.
It does not replace sustained qualification of the deployed process topology.

Before a production capacity claim, repeat these profiles with realistic data,
dependency latency and retained evidence on the intended hardware, then compare
two committed releases using the same workload identity. Keep the artifacts with
the release and investigate budget breaches, reduced successful work, growing
backlog age and repeated comparable regressions. See the
[capacity harness and release workflow](../../reference/capacity-baseline.md).

# Release-comparison workflow qualification, 2026-09-08

Two sequential 60-second sustained profiles exercised the release comparison
on the shared lab host with identical version 2 workload/hardware identities
and the target ID `current-host-concurrent-validation`:

- [Baseline measurements](2026-09-08-comparison-base-60s.json): clean source
  `062787155ebb27cc073bf50403840d32f0697672`.
- [Candidate measurements](2026-09-08-comparison-candidate-60s.json): clean source
  `5f1209267ebda642b0814e29d6636ab623f26c8a`.
- [Comparison output](2026-09-08-release-comparison-60s.json): comparator from
  `5f1209267ebda642b0814e29d6636ab623f26c8a`, exit 0, compatible contracts,
  sufficient required samples, and no flags at the 20% regression threshold.

Each run used the same one-CPU application affinity, nice 10, 1 GiB owned-tree
RSS guard, and PostgreSQL/Redis CPU and memory caps as the longer profile.
Other final test/build validation was allowed on the host during these runs.
The changing contention makes these measurements suitable for proving the
comparison workflow executes against two actual committed releases. They do
not establish that the candidate caused a performance improvement, and they
are not interchangeable with the separate 600-second quiet-window baseline.

| Observation | Baseline | Candidate |
| --- | ---: | ---: |
| Successful exports / safe policy conflicts | 29 / 2 | 30 / 1 |
| Successful AI / governance operations | 31 / 30 | 31 / 30 |
| New articles with current classification and completed IOCs | 48 | 48 |
| Successful export p95 | 585 ms | 371 ms |
| Successful AI operation p95 | 684 ms | 308 ms |
| Governance p95 | 231 ms | 118 ms |
| Observed DNS deadline p95, six probes | 100.5 ms | 101.9 ms |
| Observed header deadline p95, six probes | 103.2 ms | 102.9 ms |
| Python peak RSS | 289.3 MiB | 288.2 MiB |
| Owned-tree peak RSS, shared pages counted per process | 315.6 MiB | 314.5 MiB |
| Unexpected task/sampler errors or budget violations | 0 | 0 |

Required successful service-latency groups exceeded 20 observations and each
observed-deadline group had six. Low-count task/queue groups and policy
conflicts remain explicitly insufficient in the comparison output. Peak RSS,
queue age/depth, and lock-waiting query age are single-run observations, not
statistical confidence claims. Both profiles use the synchronous export
projection/artifact service; the later asynchronous artifact path and its
reservation-settlement fix require their own capacity workload.

See the [capacity runbook](../../reference/capacity-baseline.md) for reproduction,
contract checks, exit codes, and measurement limits. No remote workflow was
triggered; this is a local execution of the same runner/comparator contract.

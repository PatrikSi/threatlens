# Concurrent capacity baseline

Run the isolated workload from the repository root with Python 3.12, the backend
[development dependencies](../../backend/requirements-dev.txt), Linux `/proc`,
and a working Docker daemon:

```bash
env -u THREATLENS_TEST_DATABASE_URL -u THREATLENS_TEST_REDIS_URL \
  backend/.venv/bin/python backend/scripts/run_capacity_baseline.py \
  --profile smoke --output /tmp/threatlens-capacity-smoke.json
```

A successful run prints `Capacity measurements: ...`, passes pytest, and writes
JSON with `budget_violations: {}` and no task or sampler errors. Use `--profile
baseline` for the recorded workload or `--profile large` for the larger explicit
experiment. Ordinary backend test runs skip this module. The quality-gates
backend job runs smoke with a two-minute command timeout and uploads its JSON.
A skipped test cannot satisfy the runner's current-run output check.

The runner refuses inherited test database/Redis URLs. Existing test fixtures
create randomly named disposable PostgreSQL and Redis containers, migrate the
database to the current head, and remove those containers afterward. The runner
uses a temporary working directory and passes only process/Docker environment
settings to pytest, so it does not load a deployment `.env` or app credentials.
It never connects to an existing application database or broker. The default
fixture images are `postgres:16` and `redis:7-alpine`; a first run can pull those
images. `THREATLENS_TEST_POSTGRES_IMAGE` and `THREATLENS_TEST_REDIS_IMAGE` remain
available for explicit fixture image overrides.

## Workload

| Dimension | Smoke | Baseline | Large |
| --- | ---: | ---: | ---: |
| Retained articles before the burst | 30 | 500 | 2,000 |
| Text bytes per retained article | 8,192 | 32,768 | 65,536 |
| Feeds published during consumer outage | 2 | 4 | 8 |
| New articles in the burst | 8 | 60 | 200 |
| Concurrent export attempts | 4 | 20 | 40 |
| Concurrent governance updates | 4 | 20 | 40 |
| Concurrent AI connection operations | 4 | 20 | 40 |
| Local provider response delay | 60 ms | 100 ms | 150 ms |
| Celery worker threads | 2 | 4 | 4 |

One feed has three times the items of each other feed. All sources share one
local domain, exercising the actual domain concurrency limit. Retained article
bodies use repetitive synthetic ASCII text; this is compressible data, and its
size is not representative of arbitrary on-disk PostgreSQL storage.

The harness first enables enforced data policy using the real preflight and
creates a synthetic administrator, feeds, and a custom handling label. It
publishes actual `fetch_feed` messages into Redis while no consumer exists,
verifies the backlog, waits 150 ms, and starts a real Celery worker. Ingestion
uses the application feed parser, article extraction, classification, IOC
extraction, alert evaluation, and integration event routing. Completion requires
all expected articles to have saved text, current classification source
revisions, completed IOC extraction, and no queued or unacknowledged messages.
No external notification destination is configured.

Three service threads concurrently perform these operations:

- Exports alternate full article text and metadata-only JSONL, using the real
  Redis export lock, bounded database projections, artifact generation, and
  final policy fence. They target the retained catalog and remove generated
  files after each attempt.
- Governance updates revise the handling label through the application service
  and commit the corresponding policy revision.
- AI connection operations create/start durable task runs, call the real
  provider runtime against a deterministic loopback HTTP server, and persist
  the completed run, usage, and attempt receipt. The provider returns a small
  valid OpenAI-compatible response after its fixed delay.

Policy conflicts are expected under this workload. Export revision conflicts
are counted separately from successful exports. An AI pause is accepted only
for the specific repeated-authorization-change response and only when all its
receipts prove `voided` / `not_sent`. The harness does not retry provider calls
on an ambiguous outcome. After concurrent work settles, one additional export
and AI operation must succeed. Receipt states, connection task states, and
usage counts must agree with the outcomes. Other application error results,
Celery task exceptions, unexpected outcomes, and sampler errors fail the run.

## Measurements and provisional budgets

[Machine-readable budgets](../reviews/capacity/budgets.json) are regression
checks for this synthetic workload. They are provisional and do not establish
deployment sizing, a supported maximum, or a service-level agreement.

| Measurement | Smoke | Baseline | Large |
| --- | ---: | ---: | ---: |
| Successful export p95 | 5 s | 10 s | 30 s |
| Governance update p95 | 3 s | 5 s | 10 s |
| Successful AI connection p95 | 5 s | 5 s | 10 s |
| Ingestion backlog recovery | 60 s | 120 s | 240 s |
| Python process RSS increase | 256 MiB | 512 MiB | 768 MiB |
| Sampled age of a lock-waiting query | 5 s | 10 s | 10 s |

Latencies use `perf_counter` and nearest-rank p50/p95. With only a few samples,
p95 can equal the maximum and is not a stable population estimate. JSON keeps
all-attempt, successful, and policy-conflict operation groups separately. Task
latencies are from Celery prerun/postrun signals and exclude queue residence.
Backlog recovery includes worker startup and time until the classified/IOC
pipeline and broker settle. It starts after the intentional consumer outage.

A sampler starts before seeding and reads process RSS, Redis queue lengths plus
the unacknowledged-message hash, and PostgreSQL activity approximately every
20 ms. RSS includes the embedded worker and local HTTP server in one Python
process; it excludes PostgreSQL/Redis containers and other host processes.
Short memory peaks between samples can be missed. RSS increase is relative to
the loaded/migrated test process before the workload starts, not total memory
needed to run ThreatLens.

For sessions tagged `threatlens-capacity-*`, PostgreSQL samples record
`clock_timestamp() - query_start` only while `wait_event_type = 'Lock'`. This is
the **age of a query observed waiting for a lock**, an upper bound on that
query's current wait duration, not exact accumulated lock-wait time. Fast waits
between samples are missed. A zero sample count does not prove there were no
locks. One sampler connection shares the mixed harness's explicitly budgeted pool
of sixteen persistent connections with zero overflow. Runtime versions,
CPU count, platform, profile, source revision, and pool settings are recorded.
Elapsed workload time includes seeding and embedded worker startup/shutdown;
fixture provisioning and migrations happen before that timer.

## Interpreting and extending a baseline

The [2026-09-08 integrated baseline](../reviews/capacity/2026-09-08-integrated-baseline.json)
at source revision `0209678` passed all provisional budgets. It used 500 retained
32 KiB articles and a 60-article ingest burst while the three service loops ran.

| Observed measurement | Result |
| --- | ---: |
| Successful export p95 (11 successes) | 711 ms |
| Successful AI connection p95 (21 successes) | 497 ms |
| Governance update p95 (20 updates) | 216 ms |
| Backlog recovery | 17.83 s |
| Process RSS increase / sampled peak | 66.1 MiB / 312.9 MiB |
| Peak sampled age of a lock-waiting query | 2.03 s |
| Export policy conflicts | 10 of 21 attempts |

All 60 articles completed classification/IOC processing, and every successful AI
call had matching durable usage and receipt records. There were no unexpected
task/sampler errors. The export conflicts are expected safe rejections under
continuous policy changes; they are included in the JSON rather than counted
as successful fast exports. PostgreSQL 16.14, Redis 7.4.9, Python, CPU count,
platform, worker pool, and connection settings are captured in the artifact.

Keep the workload and budgets in version control. Commit the harness first,
then run `baseline` on the exact source revision to be compared and save its
JSON under `docs/reviews/capacity/`. Compare outcome counts and error states
before interpreting latency: faster safe rejections do not imply faster
successful operations. Repeat measurements on otherwise quiet, comparable
hardware before changing a budget. Record both passing and failing experiments
when investigating a regression; pytest logs contain early task failures that
can prevent complete JSON generation.

This is a bounded service workload, not a deployment load test. It does not
exercise FastAPI/nginx request queues, browser behavior, production prefork
worker processes, multiple independent API pools, a real model, report
production, webhook delivery, retention jobs, or long-lived data growth. The
threaded embedded worker uses a 200 ms heartbeat to service acknowledgement
wakeups promptly; it does not model production worker scheduling or CPU
isolation. The fixed operation loops provide overlap but do not sustain a
constant arrival rate. Consumer outage recovery demonstrates a Redis backlog
surviving absent consumers; it does not simulate a broker crash, worker death
mid-transaction, or Redis persistence recovery.

Before claiming a deployment capacity, extend the same measurements to the
actual HTTP ingress and worker topology, representative article/body
distributions, tenant and policy counts, sustained traffic, slow real-provider
behavior, and database/Redis container memory. State the chosen freshness and
interactive-latency requirements, then find the first violated budget as load
increases. The checked-in baseline is evidence for the measured workload only.

## Version 2: bounded sustained runs and release comparisons

The version 2 runner additionally supports an explicit duration:

```bash
backend/.venv/bin/python backend/scripts/run_capacity_baseline.py \
  --profile sustained --duration-seconds 600 --target-id current-host-lab \
  --cpu-count 1 --max-rss-mib 1024 \
  --output /tmp/threatlens-sustained.json
```

This profile uses 200 retained 8 KiB articles, two worker threads, one export,
governance and AI lane each paced at two seconds, and an eight-article feed
batch every ten seconds. Each lane has at most one operation in flight and
never catches up with a burst after a slow operation. The result records actual
completed counts; a slower release can complete fewer operations than the
nominal rate. A 600-second run offers at most 480 new articles. Duration is
required and bounded to 10–3,600 seconds. Retained synthetic articles start
with completed classification and known-empty IOC state. The real all-stage
processing dispatcher runs every five seconds, including missing-article repair
after a two-second grace period. These capacity-only settings accelerate recovery
to fit the 60-second drain budget; production uses a 30-second dispatch interval
and a 300-second missing-article grace period. The workload fingerprint records
both settings and the repair and completion contracts; older IOC-only artifacts
are incompatible. Recovery requires a successfully fetched, nonblank article,
current classification, completed IOC extraction, and no pending tagging, as well
as an empty broker. A drained broker alone does not establish processing recovery.
Sustained `queue.recovery_ms` measures
the drain after load stops, whereas finite burst profiles include worker
startup; these profiles cannot be automatically compared.

The runner places only its child process group on the requested count of
currently allowed CPUs and gives it nice 10. A 100 ms watchdog samples the RSS
sum of that group's owned process tree; above the configured limit, or after
the duration plus 240 seconds, it terminates only its own process group and
writes a failed result with partial measurements. Shared pages are counted for
each process, so this sum is an upper bound on unique physical memory. This is
a sampled RSS guard, not a kernel memory cgroup; a spike can occur between
samples. Each fixture PostgreSQL/Redis container has a hard 0.5 CPU quota and
512/128 MiB memory limit with no additional swap allowance. Redis uses AOF and
`appendfsync always`. Ports bind only to loopback. Cleanup records exact created
container IDs and checks the per-run label before removing them, including
watchdog exits. These bounds constrain the experiment and are recorded as
comparison inputs; they are not production sizing recommendations.

Cleanup also discovers containers by the supervisor's exact random run label,
then verifies that label on each container before removal. This covers a Docker
request accepted before its returned ID reaches the manifest. Interrupted runs
allow three seconds for late daemon completion, with a ten-second total cleanup
budget and two-second command bounds. The run label is printed for recovery.
A daemon request completing after that bounded grace, an unavailable daemon,
or a hard-killed supervisor can still require manual cleanup: inspect only
`label=threatlens.capacity.run_id=<that-run-id>`, never a broad name prefix or
unrelated deployment resources.

Publisher and worker signals record a monotonic publication timestamp in each
synthetic task header. The result includes publication-to-start queue latencies
and sampled oldest pending-message age. These are same-host measurements;
monotonic timestamps cannot be compared across different operating-system
clock domains. Six delayed DNS and six delayed response-header probes bracket
the workload. They exercise actual deadline handling and record elapsed time
until observed timeout, rather than copying nominal settings. DNS delay is
injected only for the synthetic test hostname; the socket probe uses a real
loopback server. These probes do not estimate a real provider's performance.

Compare two completed version 2 artifacts:

```bash
backend/.venv/bin/python backend/scripts/compare_capacity_runs.py \
  /tmp/previous-release.json /tmp/candidate-release.json \
  --regression-percent 20 --output /tmp/capacity-comparison.json
```

Exit 0 means compatible inputs with enough primary samples and no flagged
regression; 1 means a flagged regression; 2 means incompatible, invalid,
unlabeled, or inconclusive inputs. Successful exports, successful AI calls and
governance each require 20 samples in both runs; deadline probes require five.
Missing success groups cannot pass by substituting fast policy rejections.
The small smoke and finite baseline profiles often have too few successful
exports for a conclusive comparison; use sustained runs for release decisions.
Recovery drills remain individual fault observations and cannot establish a
latency trend from one crash. The comparison
requires identical target ID, hardware/affinity/cgroup constraints, workload,
resource bounds, runtime environment, budgets and measurement contract. Source
revisions can differ. Version 1 artifacts remain descriptive evidence and
cannot be compared automatically with version 2. Latency p95 requires at least
20 observations per group (five for deterministic deadline probes); smaller
samples are reported as insufficient. Peak and recovery deltas are explicitly
single-run observations, and zero baselines show absolute changes without
fabricated percentages. Both process RSS peak and watchdog-owned tree RSS peak
are compared, alongside growth, queue depth/age/recovery and sampled lock waits.
Outcome counts accompany every comparison so safe rejections are
visible. Repeated comparable runs and a quiet dedicated host are required
before interpreting a percentage as a release regression; the shared target
host can have unrelated contention even when its hardware fingerprint matches.

## Controlled failure recovery

The `recovery` profile runs a separate, small experiment with one real Celery
prefork child. It first accepts a feed message into its own Redis AOF broker,
issues `SIGKILL` to that exact container, restarts the same container, and
verifies that the accepted queued message is unchanged. It then starts the
worker, holds the local article response, and proves that the task holds its
Item row lock and has not committed Article content. The harness kills only
the recorded currently attached prefork child, releases the local response,
and verifies Celery redelivers the same task to a replacement child. A real
all-stage repair dispatcher also runs, including missing articles; completion requires current
classification, IOC extraction, no pending tagging, no queued/unacknowledged
messages, and one successfully fetched, nonblank Article per expected Item.
The article-repair eligibility delay is explicitly
zero in this fixture; production keeps its configured delay.

```bash
backend/.venv/bin/python backend/scripts/run_capacity_baseline.py \
  --profile recovery --target-id current-host-lab \
  --output /tmp/threatlens-recovery.json
```

The expected worker-loss log is evidence of the injected fault. Unexpected
application failures, changed/lost accepted messages, missing redelivery,
duplicate records, or recovery beyond 90 seconds fail the test. Redis restart
has a 30-second acceptance budget. This demonstrates AOF recovery of queued
work and child-process replacement. It does not simulate host power loss,
filesystem corruption, replication failover, or Redis loss during an active
provider request. Provider ambiguity and cancellation require their separate
receipt/deadline tests. Recovery's parent-process RSS excludes the prefork
worker; the runner's `watchdog.owned_process_tree_rss_peak_bytes` includes all
owned worker processes, with shared pages counted per process.

The process supervisor uses Linux subreaper support and retains the group
leader's wait status until cleanup. Even when the leader exits before a child
that ignores TERM, cleanup kills and reaps the still-owned group. It never
signals an unrelated sibling. Process setup runs from the single-threaded CLI;
embedding this supervisor in a multithreaded process would require replacing
its `preexec_fn` setup.

## Release trend workflow

The manual **Capacity release comparison** GitHub Actions workflow accepts two
committed refs and a `baseline` or `sustained` profile. Both refs must include
the version 2 harness. It builds separate interpreters before measurements,
then runs both releases sequentially on the same runner with identical caps.
Its per-job target ID prevents automatic comparison across unrelated hosted
runners. Results, logs, and compatibility/regression output are retained for
90 days. Incompatible measurement contracts fail visibly; changing start
phases, repair cadence, caps, or dataset shape requires a new baseline.

For a target installation, retain the same target ID only while its measured
hardware and resource allocation remain the same. Commit source first; each
run captures its starting revision and whether measurement/application source
was dirty, and the comparison rejects dirty source. Preserve the command,
artifact, and operational context alongside a release. On a shared host, note
other workload activity without inspecting or exporting live application data.
Two sequential hosted runs can still be noisy; the workflow supplies a
repeatable experiment, not statistical proof or a production capacity claim.

The recorded sustained export lane calls the projection/artifact services. It
does not measure the newer asynchronous export-job admission, encrypted chunk
storage, or download route. Adding those stages changes the workload contract
and requires a new baseline.

The broker fault initially reproduced an application producer deadlock in the
installed Celery 5.5.3 / redis-py 6.2.0 result-consumer reconnect path: an
`AsyncResult` finalizer unsubscribed while Redis reconnected and resubscribed
under its non-reentrant PubSub lock. ThreatLens did not consume those task
results; progress and completion were already stored in application tables.
Registered tasks now ignore unused results, and both named feed producers
explicitly set `ignore_result=True` because Celery `send_task` does not inherit
that default. The recovery test verifies zero result subscriptions/rows and
successful post-restart publication without restarting the producer. Future
features that need Celery result retrieval or chords must design and validate
that separate result-consumer lifecycle explicitly.


### Hardening workload contract

The mixed harness now uses an explicit **16-connection pool with zero overflow**
shared by its logical API, worker and measurement threads. This is an aggregate
simulation budget; deployed processes use the separate budgets in
[runtime budgets](../pages/runtime-budgets.md). Results from the old pool or
workload contract are intentionally incompatible.

Each run starts two real durable export generators concurrently, with separate
credentials and disjoint halves of the retained article catalog, alongside the
mixed workload. It checks encrypted source membership, item progress and artifact
size, then requires both exports to publish after governance changes settle.
There are at most four retained jobs. The large profile gives each lane 1,000
articles with 64 KiB bodies. Policy changes during generation may reject a job;
other failures fail the workload. These few paired samples test correctness and
contention, not a statistically meaningful asynchronous-export percentile.
Container resource isolation and prefork crash recovery are separate probes.

For release qualification, run `Capacity release comparison` with the dedicated
`threatlens-capacity` runner label and a stable `target_id` identifying the intended
hardware and cgroup profile. Use an isolated, disposable runner with Docker access
and no production mounts or credentials. This manually triggered workflow executes
both selected revisions, so only trusted maintainers should choose refs on a
self-hosted runner. The default hosted runner provides a reference comparison;
it does not certify a deployment's capacity. Retain the JSON and comparison
artifacts with the release, and repeat sustained and recovery profiles after
material worker, database, ingestion or export changes.

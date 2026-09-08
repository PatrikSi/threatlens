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
locks. One sampler connection shares the application engine's default pool of
five persistent connections plus ten overflow connections. Runtime versions,
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

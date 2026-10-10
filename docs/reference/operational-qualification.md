# Independent monitoring and deployment qualification

The host tools in `scripts/operations/` observe fleet resource pressure and recovery evidence independently of the API, PostgreSQL and Redis. They complement application health and processing freshness metrics. A successful API health check cannot satisfy a backup, independent-key or recovery-drill objective.

These tools do not install a monitoring service, send notifications, copy backups or alter an existing deployment automatically. Configure them for each deployment and collect alerts on an independent monitoring host. The checked-in example thresholds are starting values, not measured production capacity guarantees.

## Run the host monitor

Requirements are Linux, Python 3.11+, the Docker CLI and permission to inspect the selected Docker daemon. Optional HTTPS notification delivery additionally requires curl. The host monitor uses Python's standard library; receipt creation and key-recovery tests use the backend's pinned `cryptography` dependency.

Copy `scripts/operations/examples/monitor.json` to a private configuration directory. Set a unique deployment identity and the actual Compose project name. Enumerate every persistent service, including every worker role and scheduler. Only intentional one-shot services such as `migrate` belong in `ignored_services`. Unexpected services raise an inventory incident; scaled replicas are all observed.

Configure storage paths for the actual database volume filesystem, export artifacts, backup staging and other capacity-critical mounts. Merely measuring `/` does not establish space available on a separate database or backup filesystem. Each path has both a byte and percentage reserve.

```sh
python3 scripts/operations/monitor.py \
  --config /etc/threatlens-monitor/monitor.json \
  --state /var/lib/threatlens-monitor/state.json \
  --prometheus /var/lib/node_exporter/textfile_collector/threatlens.prom
```

Exit status is `0` for satisfied configured objectives, `1` for observed incidents or an unacknowledged receiver, and `2` for configuration/monitor failure. JSON on stdout includes the current observations, stable incident IDs and incident transitions. Errors expose a category rather than secret-bearing input. Keep the state directory private; state is locked and atomically replaced. JSON, state and evidence inputs must be bounded regular files, and key files must be owner-only regular files; symlinks and FIFOs are refused.

`scripts/operations/examples/threatlens-monitor.service` and `.timer` provide an opt-in systemd installation template with a 60-second interval. Adapt `/opt/threatlens`, create the account/directories and install the configuration before enabling it. **Membership in the Docker group is effectively host-administrator access.** The example uses a dedicated host account to avoid putting the Docker socket in the application. A deployment that cannot grant that access needs a separately administered, read-only inventory adapter; the example does not make the Docker socket least-privileged.

Fleet observations include aggregate memory, limits, the maximum individual replica's memory ratio, OOM state, restart counters, health and missing instances. One nearly exhausted worker cannot be hidden by idle replicas. Unknown memory limits/read failures remain unknown and raise an incident. Docker output is bounded to 1 MiB per call with a 15-second absolute deadline; stderr is discarded. Storage paths and container IDs do not become metric labels. Container IDs remain only in private restart-tracking state.

The monitor preserves previous fleet incidents while Docker is unavailable. It does not manufacture recovery from a failed observation. Restart incidents describe increases between observations; a later stable observation resolves that incident. Replacing a container resets its Docker restart counter, so retain historical metric/incident records externally.

The last observed restart time is retained separately per configured service in
private monitor state and exported as
`threatlens_host_last_restart_timestamp_seconds`. Stable samples, container
replacement and observation failures do not refresh or erase an existing event.
An initial counter, a decreased counter, or a new container identity establishes
a baseline; historical restarts are not invented as current events. Changing
the deployment identity clears the old baseline and event times. Removed
services no longer emit event metrics.

## Independent alerting and optional delivery

The Prometheus textfile is replaced atomically. `threatlens_host_observation_timestamp_seconds` is the external freshness signal; missing metric values mean unknown, not zero. Import `scripts/operations/examples/prometheus-alerts.yml` into an independently hosted Prometheus/Alertmanager installation and create a missing-observation rule for **each expected deployment identity**. A single fleet-wide `absent()` misses one disappeared host if another still reports.

`ThreatLensContainerRestarted` fires without a pending period for five minutes
after an observed restart. This catches isolated restarts even after their
one-sample incident resolves or a scrape is briefly delayed. Repeated observed
restarts refresh that window. Persistent health/recovery incidents keep their
separate two-minute pending period. Scrape/evaluation intervals and Alertmanager
delivery delays must fit the event window; longer observation outages belong to
the independent missing-monitor alert. Deploy both the updated monitor and rule
file; old monitors do not emit the new timestamp metric.

A monitor running on a failed host cannot announce its own disappearance. External scraping with a deadman alert, or an external receiver that alarms on missing heartbeat, is required. Independent infrastructure and alert delivery must be tested by the deployment owner.

An optional `receiver` object explicitly enables HTTPS delivery:

```json
{"receiver":{"url":"https://monitor.example.net/threatlens","signing_key":"/etc/threatlens-monitor/receiver.key"}}
```

URLs cannot contain credentials, query parameters or fragments. Each observation, including a healthy observation without transitions, is sent with a 10-second total deadline and no redirects. Only a 2xx response acknowledges delivery. The JSON `signature` is HMAC-SHA256 over the remaining object encoded with sorted keys, compact separators and UTF-8. Receivers must verify it, check deployment and timestamp, and deduplicate stable `event_id` values. A lost acknowledgement can repeat the same event; it cannot justify performing an external action twice.

The durable outbox retains the newest 256 transitions. Current incident state is always included, and `dropped_event_count` / `threatlens_host_dropped_events` disclose overflow. Receiver availability and pending deliveries have separate metrics. This is operational incident delivery, not the application's intelligence webhook system. No receiver is configured or contacted by repository tests.

## Deployment recovery objectives and evidence

The example requires four separately authenticated kinds of evidence:

| Kind | What is verified | Freshness basis |
| --- | --- | --- |
| `local_backup` | Existing recovery manifest and archive checksum/size | Backup snapshot time |
| `offhost_copy` | Copied archive checksum equals a separately supplied expected source digest | Backup snapshot time |
| `key_recovery` | Recovered application key decrypts an independently retained probe | Actual verification time |
| `host_loss_drill` | Reconstruction, restored-data validation, quarantine and independent key use | Original drill completion time |

Set an explicit backup interval and maximum age for each kind. A freshly re-verified old backup remains old. Re-signing an old drill result retains its original completion time, including repeated verification in the same second. Missing, invalid, future-dated, wrong-issuer, wrong-deployment or overdue receipts raise explicit recovery incidents. Receipt keys are separate from the application encryption key.

Use different trusted issuers and signing keys for local verification, the off-host receiver, key escrow and the recovery drill. The remote verifier must create the off-host receipt **after reading and verifying the received archive**. Transfer that signed receipt back to the monitor; do not infer success from a local copy command or upload acceptance. Configure expected storage-domain identities distinct from the primary host. A configured name is an administrative assertion: the software cannot prove that a directory or claimed storage domain is physically independent. Trust and access controls for each signing identity are part of the deployment boundary.

Use the actual recovery tool to create and verify backups; this monitor does not replace its role separation, journals or destructive-restore safeguards:

```sh
scripts/recovery/threatlens-recovery.sh backup --output-dir /srv/threatlens-backups

backend/.venv/bin/python scripts/operations/record_evidence.py \
  --kind local_backup --deployment threatlens-production \
  --issuer local-backup-verifier --storage-domain primary-host \
  --backup /srv/threatlens-backups/SELECTED_BACKUP \
  --signing-key /etc/threatlens-monitor/local_backup.key \
  --output /var/lib/threatlens-monitor/local_backup.json
```

On the independently administered backup receiver, use `--kind offhost_copy`, its own issuer/domain/signing key and `--expected-archive-sha256` from a separately authenticated source manifest. `--backup` must point to the copied directory containing the original manifest and archive. Both content and declared digest must match.

Before an incident, create a non-secret key probe and retain it independently from the source host:

```sh
backend/.venv/bin/python scripts/operations/record_evidence.py \
  --create-key-probe --recovered-key /secure/source-app-encryption.key \
  --output /secure/independent-app-key-probe.json
```

During a key-recovery exercise, retrieve the key through the actual independent escrow process and record `--kind key_recovery` with `--recovered-key`, `--key-probe`, deployment, issuer, storage domain, signing key and output. The tool performs decryption; a matching fingerprint alone is insufficient. The tool never prints key material or puts it in the receipt.

A host-loss receipt additionally requires a passed `host_loss_qualification` JSON artifact, the verified backup, expected archive digest, recovered key and probe. Its schema includes deployment, run ID, finish time, archive digest, immutable backend image ID, backend source digest, migration head, qualification scope and the four restoration proof flags. Receipts retain the artifact digest and application identity. Configure `backend_source_sha256` on the drill objective when a particular release must have passed.

Production drill objectives default to `qualification_scope: independent_target` and require `production_qualified: true`. **The repository's local reconstruction simulation deliberately cannot satisfy this objective**, even if someone changes its deployment name to a production name. For local tool testing only, explicitly select `disposable_local_fault_domain_simulation` in a separate test policy. A production owner must run and attest a restoration on independently administered target infrastructure before publishing independent-target evidence; signing a claim does not itself perform that drill.

## Qualify the local ingress and worker topology

Install the backend development environment and ensure `postgres:16`, `redis:7-alpine`, and the chosen ThreatLens web image already exist locally. The runner refuses implicit image pulls and externally supplied test database/broker/browser URLs. It creates randomly named, owned containers and loopback ports, snapshots current backend source, and starts separate API, processing, maintenance, notification, export and beat processes. Requests pass through the actual nginx configuration. It never reads the deployment `.env` or connects to deployment databases.

```sh
backend/.venv/bin/python scripts/operations/qualify_local_topology.py \
  --duration-seconds 300 --concurrency 4 \
  --target-id local-development-host \
  --web-image threatlens-web:dev \
  --max-p95-ms 2000 --max-rss-mib 3072 \
  --output /tmp/threatlens-local-qualification.json
```

The workload authenticates with real session cookies and CSRF, prepares 40 stable export articles, and queues 40 separate classification/IoC items. Two disjoint exports wait while their dedicated worker is paused, then recover after it starts. Every 60 seconds, when the previous round has completed and at least 30 seconds remain, another 40 processing articles and two exports are submitted, up to six rounds. Four concurrent readers exercise article listings, statistics and export listings throughout. Each export contains 20 full 21,600-character articles; every downloaded ID, count and body length is verified, and the two groups must be disjoint within each round. These are bounded fixtures, not a claim about gigabyte-scale exports.

Pass criteria include no HTTP errors, the configured p95 and aggregate process RSS budgets, recovery of every round, fresh execution canaries for all four worker queues, process liveness and satisfied independent container observations. Aggregate RSS includes shared pages counted once per process and is intentionally conservative. The runner records per-role samples, backlog counts, recovery times, image IDs, source revision/dirty status, exact backend snapshot and nginx hashes. Failed runs retain bounded private diagnostic logs beside the artifact and clean up owned resources. The API and workers are separate host processes in this harness; the database, broker and nginx are resource-limited containers. This differs from a fully containerized production topology and is recorded as local-only qualification.

A 30/60-second run is a smoke test. A five-minute run exercises repeated local work and recovery, while release qualification should use the intended hardware, sustained representative volumes, actual ingress/TLS and worker constraints, realistic source sizes and independently monitored storage. This harness disables AI and real external I/O; it does not qualify provider latency, remote SIEM reliability, production alert delivery, Kubernetes or multi-host scheduling.

## Reconstruct after losing disposable source storage

The opt-in test uses the actual recovery adapter, limited database roles and quarantine hook. It backs up a database containing an encrypted secret, copies the archive and key probe into separate temporary test storage, destroys source containers and volumes, discards source credentials, constructs fresh database/broker infrastructure with new credentials, and restores/decrypts using the independently retained key. The supplied backend image must contain the exact current app and migration source; the artifact records its immutable ID and actual migration head.

```sh
THREATLENS_RUN_HOST_LOSS_QUALIFICATION=1 \
RECOVERY_E2E_BACKEND_IMAGE=threatlens-backend:current-source-test \
THREATLENS_HOST_LOSS_OUTPUT=/tmp/threatlens-host-loss.json \
backend/.venv/bin/python -m unittest discover \
  --start-directory tests/operations --pattern test_host_loss_drill.py --verbose
```

Use a freshly built image or a source-only overlay on an existing image with identical locked dependencies. The test refuses a stale image's source digest. All resources belong to an isolated test project and are removed afterward. The artifact explicitly says `production_qualified: false` and `scope: disposable_local_fault_domain_simulation`: separate directories on one host demonstrate reconstruction logic, not physical off-host durability. A lost-host exercise on the intended independent target, key-vault access test, backup transfer objectives and external deadman alert test remain deployment-specific acceptance work.

## Regression checks

```sh
backend/.venv/bin/python -m unittest discover \
  --start-directory tests/operations --pattern 'test_*.py' --verbose
backend/.venv/bin/ruff check scripts/operations tests/operations
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=64m \
  --mount type=bind,src="$PWD",dst=/work,readonly --workdir /work \
  --entrypoint /bin/promtool \
  prom/prometheus:v3.13.3@sha256:6976aa8a60fec930796ce5772b8d12da7a318a5daa8d40d69c5c7819a05eeed7 \
  test rules tests/operations/prometheus-alerts.test.yml
```

The rule fixtures use [Prometheus's rule test format](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)
and cover immediate firing, incident resolution, expiry, repeated events, delayed
or missing scrapes, and future timestamps. Python tests cover counter resets,
container replacement, deployment changes and unavailable fleet observations.
CI runs both checks.

The lightweight operations checks run in the existing CI recovery job. The reconstruction test is opt-in locally and explicitly enabled in the existing CI disposable recovery job; the local sustained runner produces reviewable evidence and is not silently run against a developer's live stack.

## Recorded local qualification

The [26 September 2026 local topology artifact](../reviews/capacity/2026-09-26-local-topology.json) records a passed 300-second run through nginx with five mixed-work rounds: 6,259 HTTP requests, no HTTP errors, p95 89.2 ms, 200 newly processed articles, ten verified exports, worst backlog recovery 9.37 seconds and a 2.02 GB aggregate process RSS peak. The artifact includes all samples, image identities and exact backend snapshot hash. It ran from a dirty development checkout at revision `fd4544ec7df3ffd4155fcd25360c1569a5ec6b11`; it is evidence for this local harness and source snapshot, not a production release capacity claim.

The [same-day source-loss artifact](../reviews/capacity/2026-09-26-host-loss.json) records a passed 128.5-second reconstruction using the current-source backend image and migration head `0115_reviewed_publications`. The original disposable volumes and credentials were removed before restoration; recovered encrypted data, outbound quarantine and runtime privilege restrictions were checked afterward. The existing CI disposable recovery job now repeats this test and uploads its artifact. The test remains a local fault-domain simulation and cannot satisfy the independent-target production policy.

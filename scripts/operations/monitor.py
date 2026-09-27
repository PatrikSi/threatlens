#!/usr/bin/env python3
"""Independent host/fleet/recovery monitoring; never requires the ThreatLens API."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
from urllib.parse import urlsplit

from evidence import DRILL_SCOPES, KINDS, LABEL, canonical, check_recovery, read_json, read_key, sign
from fleet import observe_fleet, observe_storage


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("Unsupported monitor configuration schema")
    for name in ("deployment", "compose_project"):
        if not isinstance(config.get(name), str) or not LABEL.fullmatch(config[name]):
            raise ValueError("Deployment and project require bounded identifiers")
    services = config.get("services")
    if not isinstance(services, dict) or not 1 <= len(services) <= 32:
        raise ValueError("Configure between 1 and 32 expected services")
    for name, service in services.items():
        if not isinstance(name, str) or not LABEL.fullmatch(name) or not isinstance(service, dict):
            raise ValueError("Invalid service objective")
        if type(service.get("min_instances")) is not int or not 1 <= service["min_instances"] <= 32:
            raise ValueError("Service minimum instances must be between 1 and 32")
        ratio(service.get("memory_warning_ratio"))
    ignored = config.get("ignored_services", [])
    if not isinstance(ignored, list) or len(ignored) > 32 or any(not isinstance(name, str) or not LABEL.fullmatch(name) for name in ignored):
        raise ValueError("Ignored one-shot services require bounded names")
    storage = config.get("storage", {})
    if not isinstance(storage, dict) or len(storage) > 16:
        raise ValueError("Configure at most 16 host storage objectives")
    for name, objective in storage.items():
        if (not isinstance(name, str) or not LABEL.fullmatch(name) or not isinstance(objective, dict)
                or not isinstance(objective.get("path"), str) or not Path(objective["path"]).is_absolute()):
            raise ValueError("Storage requires a bounded label and absolute path")
        if type(objective.get("min_free_bytes")) is not int or objective["min_free_bytes"] < 0:
            raise ValueError("Storage free bytes must be a nonnegative integer")
        ratio(objective.get("min_free_ratio"))
    policy = config.get("recovery", {})
    if not isinstance(policy, dict):
        raise ValueError("Recovery policy must be an object")
    if policy:
        if not isinstance(policy.get("primary_storage_domain"), str) or not LABEL.fullmatch(policy["primary_storage_domain"]):
            raise ValueError("Recovery requires the primary storage-domain identity")
        if type(policy.get("backup_interval_seconds")) is not int or policy["backup_interval_seconds"] < 60:
            raise ValueError("Recovery requires an explicit backup interval of at least 60 seconds")
        if not isinstance(policy.get("evidence"), dict) or set(policy["evidence"]) != KINDS:
            raise ValueError("Recovery policy must define local backup, off-host copy, key and host-loss drill objectives")
        for kind, objective in policy["evidence"].items():
            if not isinstance(objective, dict):
                raise ValueError("Recovery evidence objective must be an object")
            if type(objective.get("max_age_seconds")) is not int or not 60 <= objective["max_age_seconds"] <= 366 * 86400:
                raise ValueError("Evidence maximum age must be between one minute and one year")
            for name in ("receipt", "verification_key"):
                if not isinstance(objective.get(name), str) or not Path(objective[name]).is_absolute():
                    raise ValueError("Evidence paths must be absolute")
            if kind in {"offhost_copy", "key_recovery", "host_loss_drill"} and (
                not isinstance(objective.get("storage_domain"), str)
                or not LABEL.fullmatch(objective["storage_domain"])
                or objective["storage_domain"] == policy["primary_storage_domain"]
            ):
                raise ValueError("Independent recovery needs a distinct configured storage domain")
            if kind == "host_loss_drill" and objective.get("qualification_scope", "independent_target") not in DRILL_SCOPES:
                raise ValueError("Unknown recovery qualification scope")
        if policy["evidence"]["local_backup"]["max_age_seconds"] < policy["backup_interval_seconds"]:
            raise ValueError("Backup maximum age cannot be shorter than its scheduled interval")
    receiver = config.get("receiver")
    if receiver is not None and not isinstance(receiver, dict):
        raise ValueError("Receiver configuration must be an object")
    if receiver:
        if not isinstance(receiver.get("url"), str):
            raise ValueError("Receiver requires a URL")
        target = urlsplit(receiver["url"])
        if target.scheme != "https" or not target.hostname or target.username or target.password or target.query or target.fragment:
            raise ValueError("Monitoring receivers require HTTPS without URL credentials, query strings or fragments")
        if not isinstance(receiver.get("signing_key"), str) or not Path(receiver["signing_key"]).is_absolute():
            raise ValueError("Receiver signing key path must be absolute")
    return config


def ratio(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
        raise ValueError("Ratios must be greater than zero and less than one")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def incident_id(deployment: str, incident: dict) -> str:
    return hashlib.sha256(canonical({"deployment": deployment, **incident})).hexdigest()[:24]


def reconcile(config: dict, previous: dict, incidents: list[dict], *, now: datetime,
              unknown_scopes: set[str] | None = None) -> tuple[dict, list[dict]]:
    if previous.get("deployment") != config["deployment"]:
        previous = {}
    old = previous.get("active", {})
    current = {incident_id(config["deployment"], entry): entry for entry in incidents}
    for identity, entry in old.items():
        if entry["scope"] in (unknown_scopes or set()):
            current.setdefault(identity, {key: entry[key] for key in ("scope", "entity", "code")})
    events, active = [], {}
    sequence = previous.get("sequence", 0)
    epoch = previous.get("epoch") or uuid.uuid4().hex
    for identity, entry in sorted(current.items()):
        active[identity] = {**entry, "first_seen": old.get(identity, {}).get("first_seen", now.isoformat()),
                            "last_seen": now.isoformat()}
        if identity not in old:
            sequence += 1
            events.append({"event_id": f"{config['deployment']}:{epoch}:{sequence}", "incident_id": identity,
                           "state": "opened", "at": now.isoformat(), **entry})
    for identity, entry in sorted(old.items()):
        if identity not in current:
            sequence += 1
            events.append({"event_id": f"{config['deployment']}:{epoch}:{sequence}", "incident_id": identity,
                           "state": "resolved", "at": now.isoformat(),
                           **{key: entry[key] for key in ("scope", "entity", "code")}})
    pending = [*previous.get("pending", []), *events] if config.get("receiver") else []
    dropped = previous.get("dropped_events", 0) + max(0, len(pending) - 256)
    return {"schema_version": 1, "deployment": config["deployment"], "epoch": epoch, "sequence": sequence,
            "active": active, "pending": pending[-256:], "dropped_events": dropped}, events


def send_receiver(receiver: dict, payload: dict) -> bool:
    # curl's absolute timeout covers DNS/connect/streaming. Never invoke a shell,
    # follow redirects, put credentials in URLs or expose response bodies.
    signed = sign(payload, read_key(Path(receiver["signing_key"])))
    result = subprocess.run([
        "curl", "--silent", "--fail", "--max-time", "10", "--connect-timeout", "3",
        "--proto", "=https", "--max-redirs", "0", "--request", "POST",
        "--header", "Content-Type: application/json", "--data-binary", "@-", "--output", os.devnull,
        "--write-out", "%{http_code}",
        receiver["url"],
    ], input=canonical(signed), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12, check=False)
    return result.returncode == 0 and result.stdout.isdigit() and 200 <= int(result.stdout) < 300


def collect(config: dict, previous: dict, *, now: datetime, fleet=observe_fleet,
            storage=observe_storage, recovery=check_recovery) -> tuple[dict, dict]:
    if previous.get("deployment") != config["deployment"]:
        previous = {}
    # A restart is an event, not a persistent unhealthy state. Retain when it
    # was observed so a later scrape can alert after the incident has resolved.
    last_restarts = {service: observed for service, observed in previous.get("last_restarts", {}).items()
                     if service in config["services"]}
    incidents, unknown = [], set()
    try:
        fleet_metrics, fleet_incidents, counters = fleet(config)
        incidents.extend(fleet_incidents)
        old_counters = previous.get("container_counters", {})
        for identity, counter in counters.items():
            if identity in old_counters and counter["restarts"] > old_counters[identity]["restarts"]:
                incidents.append({"scope": "fleet", "entity": counter["service"], "code": "container_restarted"})
                last_restarts[counter["service"]] = now.timestamp()
        fleet_available = True
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError):
        fleet_metrics, counters, fleet_available = {}, previous.get("container_counters", {}), False
        incidents.append({"scope": "monitor", "entity": "docker", "code": "observation_unavailable"})
        unknown.add("fleet")
    storage_metrics, storage_incidents = storage(config)
    incidents.extend(storage_incidents)
    recovery_metrics, recovery_incidents = recovery(config.get("recovery", {}), deployment=config["deployment"], now=now)
    incidents.extend(recovery_incidents)
    state, events = reconcile(config, previous, incidents, now=now, unknown_scopes=unknown)
    state["container_counters"] = counters
    state["last_restarts"] = last_restarts
    report = {"schema_version": 1, "deployment": config["deployment"], "observed_at": now.isoformat(),
        "fleet_available": fleet_available, "fleet": fleet_metrics, "storage": storage_metrics,
        "recovery": recovery_metrics, "active_incidents": state["active"], "events": events,
        "pending_delivery_count": len(state["pending"]), "dropped_event_count": state["dropped_events"],
        "last_restarts": last_restarts,
        "application_health_inferred": False}
    return report, state


def prometheus(report: dict) -> str:
    lines = ["# Low-cardinality host monitor observations; absent metrics are unknown."]
    labels = f'deployment={json.dumps(report["deployment"])}'
    lines += [f'threatlens_host_observation_timestamp_seconds{{{labels}}} {datetime.fromisoformat(report["observed_at"]).timestamp()}',
              f'threatlens_host_fleet_available{{{labels}}} {int(report["fleet_available"])}',
              f'threatlens_host_active_incidents{{{labels}}} {len(report["active_incidents"])}',
              f'threatlens_host_pending_deliveries{{{labels}}} {report["pending_delivery_count"]}']
    lines.append(f'threatlens_host_dropped_events{{{labels}}} {report.get("dropped_event_count", 0)}')
    for service, observed in report.get("last_restarts", {}).items():
        lines.append(f'threatlens_host_last_restart_timestamp_seconds{{{labels},entity={json.dumps(service)}}} {observed}')
    if "receiver_delivered" in report:
        lines.append(f'threatlens_host_receiver_available{{{labels}}} {int(report["receiver_delivered"])}')
    for group in ("fleet", "storage", "recovery"):
        for entity, metrics in report[group].items():
            for name, value in metrics.items():
                if isinstance(value, (int, float)):
                    lines.append(f'threatlens_host_{group}_{name}{{{labels},entity={json.dumps(entity)}}} {int(value) if isinstance(value, bool) else value}')
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--prometheus", type=Path)
    args = parser.parse_args()
    try:
        config = validate_config(read_json(args.config))
        args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(str(args.state) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            previous = read_json(args.state) if args.state.exists() else {}
            report, state = collect(config, previous, now=datetime.now(timezone.utc))
            atomic_json(args.state, state)  # Durably enqueue before optional I/O.
            if config.get("receiver"):
                try:
                    delivered = send_receiver(config["receiver"], {**report, "events": state["pending"]})
                except (OSError, ValueError, subprocess.SubprocessError):
                    delivered = False
                report["receiver_delivered"] = delivered
                if delivered:
                    state["pending"] = []
                    report["pending_delivery_count"] = 0
                    atomic_json(args.state, state)
            if args.prometheus:
                # The target may be node_exporter's textfile directory. Atomic
                # rename prevents scrapes observing partially written metrics.
                args.prometheus.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".threatlens-metrics-", dir=args.prometheus.parent)
                try:
                    with os.fdopen(fd, "w") as handle:
                        handle.write(prometheus(report))
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temporary, 0o644)
                    os.replace(temporary, args.prometheus)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            print(json.dumps(report, sort_keys=True))
            return 1 if state["active"] or report.get("receiver_delivered") is False else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"schema_version": 1, "monitor_available": False, "error": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

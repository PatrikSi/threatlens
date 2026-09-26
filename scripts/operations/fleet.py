"""Bounded host Docker and filesystem observations, aggregated by configured role."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import time

INSPECT_FORMAT = ('{"id":{{json .Id}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"running":{{.State.Running}},"status":{{json .State.Status}},"oom":{{.State.OOMKilled}},'
    '"restarts":{{.RestartCount}},"limit":{{.HostConfig.Memory}},'
    '"health":{{with index .State "Health"}}{{json .Status}}{{else}}"none"{{end}}}')


def docker(*arguments: str) -> str:
    # Inventory is privileged host input, but still cannot consume unlimited
    # memory or retain an observation forever. Discard diagnostics, which can
    # include paths or daemon-specific configuration.
    deadline = time.monotonic() + 15
    with subprocess.Popen(["docker", *arguments], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise RuntimeError("Docker observation exceeded its deadline")
                    block = os.read(process.stdout.fileno(), 65536)
                    if not block:
                        break
                    output.extend(block)
                    if len(output) > 1_048_576:
                        raise RuntimeError("Docker observation exceeded its size limit")
            if process.wait(timeout=max(.001, deadline - time.monotonic())):
                raise RuntimeError("Docker observation unavailable")
            return output.decode("utf-8")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


def memory_bytes(value: str) -> int:
    match = re.fullmatch(r"([0-9.]+)\s*(B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)", value.strip())
    if match is None:
        raise ValueError("Unsupported Docker memory unit")
    units = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
             "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    return int(float(match[1]) * units[match[2]])


def observe_fleet(config: dict, *, invoke=docker) -> tuple[dict, list[dict], dict]:
    identities = invoke("ps", "-a", "--filter", f"label=com.docker.compose.project={config['compose_project']}",
                        "--format", "{{.ID}}").split()
    if len(identities) > 256 or any(not re.fullmatch(r"[0-9a-f]{12,64}", value) for value in identities):
        raise ValueError("Fleet inventory exceeds its bounds")
    rows = [json.loads(line) for line in invoke("inspect", "--format", INSPECT_FORMAT, *identities).splitlines()] if identities else []
    running = [row["id"] for row in rows if row["running"]]
    stats = [json.loads(line) for line in invoke("stats", "--no-stream", "--format", "{{json .}}", *running).splitlines()] if running else []
    memory = {row["ID"]: memory_bytes(row["MemUsage"].split("/")[0]) for row in stats}
    metrics, incidents, counters = {}, [], {}
    if any(row["service"] not in config["services"] and row["service"] not in config.get("ignored_services", []) for row in rows):
        incidents.append({"scope": "fleet", "entity": "inventory", "code": "unconfigured_service"})
    for service, objective in config["services"].items():
        instances = [row for row in rows if row["service"] == service]
        active = [row for row in instances if row["running"]]
        usage = [next((value for identity, value in memory.items() if row["id"].startswith(identity)), None) for row in active]
        known = all(value is not None for value in usage)
        limit = sum(row["limit"] for row in active) if active and all(row["limit"] > 0 for row in active) else None
        used = sum(usage) if known else None
        ratios = [value / row["limit"] for row, value in zip(active, usage)
                  if value is not None and row["limit"] > 0]
        metrics[service] = {"instances": len(instances), "running": len(active),
            "memory_bytes": used, "memory_limit_bytes": limit,
            "memory_ratio": used / limit if used is not None and limit else None,
            "max_instance_memory_ratio": max(ratios) if ratios else None,
            "oom_instances": sum(bool(row["oom"]) for row in instances),
            "restart_count": sum(row["restarts"] for row in instances)}
        codes = []
        if len(active) < objective["min_instances"]:
            codes.append("missing_service")
        if any(row["health"] == "unhealthy" or row["status"] == "restarting" for row in instances):
            codes.append("unhealthy_service")
        if metrics[service]["oom_instances"]:
            codes.append("container_oom")
        if ratios and max(ratios) >= objective["memory_warning_ratio"]:
            codes.append("memory_pressure")
        if not known or (active and limit is None):
            codes.append("memory_budget_unknown")
        counters.update({row["id"]: {"service": service, "restarts": row["restarts"]} for row in instances})
        incidents.extend({"scope": "fleet", "entity": service, "code": code} for code in codes)
    return metrics, incidents, counters


def observe_storage(config: dict, *, disk_usage=shutil.disk_usage) -> tuple[dict, list[dict]]:
    metrics, incidents = {}, []
    for label, objective in config.get("storage", {}).items():
        try:
            usage = disk_usage(Path(objective["path"]))
            metrics[label] = {"available": True, "free_bytes": usage.free, "total_bytes": usage.total}
            if usage.free < objective["min_free_bytes"] or usage.free / usage.total < objective["min_free_ratio"]:
                incidents.append({"scope": "storage", "entity": label, "code": "storage_pressure"})
        except (OSError, ValueError, ZeroDivisionError):
            metrics[label] = {"available": False, "free_bytes": None, "total_bytes": None}
            incidents.append({"scope": "storage", "entity": label, "code": "storage_unavailable"})
    return metrics, incidents

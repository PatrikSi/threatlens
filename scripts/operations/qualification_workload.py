"""Authenticated mixed HTTP load through the real proxy, with bounded samples."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import statistics
import threading
import time
import uuid


def run_workload(client, control, topology, *, duration: int, concurrency: int,
                 start_exports, max_rss_bytes: int) -> dict:
    identity = control("users")
    login = client.post("/api/v1/auth/login", json={"email": identity["email"], "password": identity["password"]})
    login.raise_for_status()
    csrf = client.cookies.get("threatlens_csrf")
    if not csrf:
        raise RuntimeError("Real authentication did not issue a CSRF token")
    client.headers["x-csrf-token"] = csrf
    seeded = control("qualification-seed")
    control("qualification-dispatch")
    warmup_deadline = time.monotonic() + 60
    while time.monotonic() < warmup_deadline:
        progress = control("qualification-progress", method="GET")
        if progress["classified"] == progress["total"] and progress["with_iocs"] == progress["total"]:
            break
        time.sleep(.5)
    else:
        raise RuntimeError("Synthetic export source preparation did not complete")
    # Concurrent processing uses another source set: modifying the exact accepted
    # export evidence is intentionally rejected by the application snapshot fence.
    processing = control("qualification-seed?stage=processing")
    queued_at = time.monotonic()
    dispatched = control("qualification-dispatch")
    def admit_exports() -> list[str]:
        identities = []
        for group in (0, 1):
            response = client.post("/api/v1/exports/jobs", json={"format": "jsonl",
                "idempotency_key": str(uuid.uuid4()), "filters": {"q": f"exports-group{group}"},
                "options": {"include_article_text": True}})
            if response.status_code != 202:
                raise RuntimeError(f"Background export admission returned {response.status_code}")
            identities.append(response.json()["id"])
        return identities
    jobs = admit_exports()
    if any(client.get(f"/api/v1/exports/jobs/{job}").json()["status"] != "queued" for job in jobs):
        raise RuntimeError("Paused export worker did not retain accepted durable work")
    # Queue admission and API reads continue before the independent export slot
    # starts. The elapsed recovery metric includes this controlled interruption.
    time.sleep(2)
    start_exports()
    measurements, errors, samples = [], [], []
    lock = threading.Lock()
    stop = threading.Event()
    started = time.monotonic()
    paths = ("/api/v1/items?limit=25", "/api/v1/stats/overview", "/api/v1/exports/jobs")

    def reader(index: int) -> None:
        count = index
        while not stop.is_set() and time.monotonic() - started < duration:
            before = time.monotonic()
            try:
                response = client.get(paths[count % len(paths)])
                status = response.status_code
            except Exception:
                status = 0
            elapsed = (time.monotonic() - before) * 1000
            with lock:
                if len(measurements) < 100_000:
                    measurements.append(elapsed)
                if status != 200 and len(errors) < 100:
                    errors.append({"path_class": count % len(paths), "status": status})
            count += 1
            stop.wait(.15)

    recovered = None
    recoveries, artifacts = [], []
    rounds, next_round = 1, 60
    processing_count = len(processing["item_ids"])
    with ThreadPoolExecutor(max_workers=concurrency) as pool, ExitStack() as cleanup:
        # Signal readers before executor shutdown even when an HTTP/control
        # observation raises, so a failed run does not retain load until duration.
        cleanup.callback(stop.set)
        futures = [pool.submit(reader, index) for index in range(concurrency)]
        while time.monotonic() - started < duration:
            memory = topology.memory()
            if sum(memory.values()) > max_rss_bytes:
                stop.set()
                raise RuntimeError("Disposable process memory exceeded the qualification budget")
            progress = control("qualification-progress", method="GET")
            statuses = [client.get(f"/api/v1/exports/jobs/{job}").json()["status"] for job in jobs]
            if any(status in {"failed", "cancelled", "expired"} for status in statuses):
                stop.set()
                raise RuntimeError("A background export failed during qualification")
            if (all(status == "ready" for status in statuses) and progress["classified"] == progress["total"]
                    and progress["with_iocs"] == progress["total"]
                    and len(progress["queue_execution_fresh"]) == 4 and all(progress["queue_execution_fresh"].values())):
                recovered = recovered or (time.monotonic() - queued_at) * 1000
            elapsed = time.monotonic() - started
            if recovered is not None and elapsed >= next_round and duration - elapsed >= 30 and rounds < 6:
                artifacts.extend(download_exports(client, jobs, seeded, round_number=rounds))
                recoveries.append(recovered)
                processing_count += len(control("qualification-seed?stage=processing")["item_ids"])
                queued_at = time.monotonic()
                control("qualification-dispatch")
                jobs = admit_exports()
                recovered = None
                rounds += 1
                next_round += 60
            samples.append({"elapsed_seconds": round(time.monotonic() - started, 3),
                "memory_bytes_by_process_role": memory, "classification_pending": progress["total"] - progress["classified"],
                "ioc_pending": progress["total"] - progress["with_iocs"], "exports_pending": sum(status != "ready" for status in statuses)})
            stop.wait(1)
        stop.set()
        for future in futures:
            future.result(timeout=20)
    if recovered is None:
        raise RuntimeError("Accepted processing/export backlog did not recover within the qualification interval")
    artifacts.extend(download_exports(client, jobs, seeded, round_number=rounds))
    recoveries.append(recovered)
    ordered = sorted(measurements)
    return {"duration_seconds": round(time.monotonic() - started, 3), "http_requests": len(ordered),
        "http_errors": errors, "latency_p50_ms": statistics.median(ordered),
        "latency_p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * .95))],
        "backlog_recovery_ms": max(recoveries), "recovery_ms_by_round": recoveries,
        "mixed_workload_rounds": rounds, "round_interval_seconds": 60,
        "memory_peak_bytes": max(sum(sample["memory_bytes_by_process_role"].values()) for sample in samples),
        "samples": samples, "seeded_export_items": len(seeded["item_ids"]),
        "seeded_processing_items": processing_count, "article_chars_each": seeded["article_chars_each"],
        "initial_dispatched": dispatched, "disjoint_export_artifacts": artifacts}


def download_exports(client, jobs: list[str], seeded: dict, *, round_number: int) -> list[dict]:
    artifacts = []
    published_ids = set()
    for group, job in enumerate(jobs):
        response = client.get(f"/api/v1/exports/jobs/{job}/download")
        response.raise_for_status()
        if len(response.content) < 100_000:
            raise RuntimeError("Qualification export did not contain the expected full article workload")
        identities = validate_export(response.content, expected=set(seeded["groups"][str(group)]),
                                     article_chars=seeded["article_chars_each"])
        if identities & published_ids:
            raise RuntimeError("The supposedly disjoint exports overlap")
        published_ids.update(identities)
        artifacts.append({"round": round_number, "source_group": group, "item_count": len(identities),
                          "size_bytes": len(response.content), "sha256": hashlib.sha256(response.content).hexdigest()})
    return artifacts


def validate_export(body: bytes, *, expected: set[str], article_chars: int) -> set[str]:
    if len(body) > 10 * 1024**2:
        raise RuntimeError("Qualification export exceeded its bounded artifact size")
    rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    identities = {row["id"] for row in rows}
    if identities != expected or len(rows) != len(expected):
        raise RuntimeError("Export identities/counts do not match the exact selected source group")
    if any(len((row.get("article") or {}).get("text") or "") != article_chars for row in rows):
        raise RuntimeError("Export did not preserve the complete selected article text")
    return identities

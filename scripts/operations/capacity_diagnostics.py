"""Bounded numeric observations; never serialize SQL, bindings or database PIDs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import threading
import time
from contextlib import contextmanager
from pathlib import Path

CONTRACT = "threatlens-capacity-diagnostics-v1"
FILES = ("capacity_diagnostics.py", "capacity_diagnostics_plugin.py")
LIMITS = {"operations": 12000, "lanes": 32, "queries": 40000, "locks": 40000, "host": 4000, "transactions": 40000}
WAIT_SQL = """
WITH waiting AS MATERIALIZED (
 SELECT pid, pg_blocking_pids(pid) AS blockers FROM pg_stat_activity
 WHERE datname=current_database() AND wait_event_type='Lock'
 AND application_name LIKE 'threatlens-capacity%'
)
SELECT a.pid, a.query, a.wait_event, w.blockers,
 extract(epoch FROM clock_timestamp()-a.query_start)*1000,
 extract(epoch FROM clock_timestamp()-a.xact_start)*1000
FROM pg_stat_activity a LEFT JOIN waiting w ON a.pid=w.pid
WHERE a.pid IN (SELECT pid FROM waiting UNION SELECT unnest(blockers) FROM waiting)
"""


def observer_identity(directory):
    hashes = {name: hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest() for name in FILES}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest, hashes


def structural_sql(statement):
    """Discard values/comments, including PostgreSQL escapes/nested comments."""
    tokens, index = [], 0
    while index < len(statement):
        rest = statement[index:]
        if rest.startswith("--"):
            end = statement.find("\n", index)
            index = len(statement) if end < 0 else end + 1
        elif rest.startswith("/*"):
            depth, index = 1, index + 2
            while index < len(statement) and depth:
                if statement[index:index + 2] == "/*":
                    depth, index = depth + 1, index + 2
                elif statement[index:index + 2] == "*/":
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
        elif rest[0] == "'" or (rest[0].lower() == "e" and rest[1:2] == "'"):
            escaped = rest[0] != "'"
            index += 2 if escaped else 1
            while index < len(statement):
                if escaped and statement[index] == "\\":
                    index += 2
                elif statement[index:index + 2] == "''":
                    index += 2
                elif statement[index] == "'":
                    index += 1
                    break
                else:
                    index += 1
        elif rest[0] == "$" and (match := re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", rest)):
            delimiter = match[0]
            end = statement.find(delimiter, index + len(delimiter))
            index = len(statement) if end < 0 else end + len(delimiter)
        elif rest[0] == '"':
            index, value = index + 1, ""
            while index < len(statement):
                if statement[index:index + 2] == '""':
                    value, index = value + '"', index + 2
                elif statement[index] == '"':
                    index += 1
                    break
                else:
                    value, index = value + statement[index], index + 1
            tokens.append(value if re.fullmatch(r"[a-z_][a-z0-9_]*", value) else "_quoted_identifier")
        elif match := re.match(r"[A-Za-z_][A-Za-z0-9_]*", rest):
            tokens.append(match[0].lower())
            index += len(match[0])
        else:
            if not rest[0].isspace():
                tokens.append(rest[0])
            index += 1
    return " ".join(tokens)


def is_sampler_sql(statement):
    value = structural_sql(statement)
    return value.startswith("select ") and bool(re.search(r"\bfrom\s+pg_stat_activity\b", value)) and bool(re.search(r"\bclock_timestamp\s*\(\s*\)\s*-\s*query_start\b", value)) and "wait_event_type" in value


def classify_sql(statement):
    lowered = statement.lower()
    if not any(name in lowered for name in ("data_policy_state", "handling_labels", "iam_policy_state")):
        return "other"
    value = structural_sql(statement)
    table = r"(?:[a-z_][a-z0-9_]*\s*\.\s*)?"
    if re.search(r"\bupdate\s+" + table + r"data_policy_state\b", value):
        return "policy_update"
    for name, exclusive, shared, plain in [
        ("data_policy_state", "policy_exclusive", "policy_shared", "policy_read"),
        ("handling_labels", "label_exclusive", "other", "other"),
        ("iam_policy_state", "other", "iam_shared", "other"),
    ]:
        if re.search(r"\bfrom\s+" + table + name + r"\b", value):
            if re.search(r"\bfor\s+key\s+share\b", value):
                return "other"
            return exclusive if re.search(r"\bfor\s+update\b", value) else shared if re.search(r"\bfor\s+share\b", value) else plain
    return "other"


def backend_pid(connection):
    driver = getattr(getattr(connection, "connection", None), "driver_connection", connection)
    return int(driver.info.backend_pid)


class Recorder:
    def __init__(self, *, clock=time.monotonic_ns, limits=None):
        self.clock, self.epoch = clock, clock()
        self.limit = dict(LIMITS if limits is None else limits)
        self.data = {name: [] for name in self.limit}
        self.dropped = {name: 0 for name in self.limit}
        self.errors, self.aliases, self.fences, self.ending = [], {}, {}, set()
        self.lock, self.local = threading.RLock(), threading.local()
        self.last_lock_sample = self.last_host_sample = -10**18
        self.sampler_count = self.sampler_gap_max = self.sampler_gap_sum = 0
        self.last_sampler = None
        self.overhead = {}

    def now(self):
        return self.clock() - self.epoch

    def error(self, phase, error):
        with self.lock:
            if len(self.errors) < 100:
                self.errors.append({"phase": phase, "error_type": type(error).__name__})

    def add(self, kind, value):
        with self.lock:
            if len(self.data[kind]) >= self.limit[kind]:
                self.dropped[kind] += 1
            else:
                self.data[kind].append(value)

    def observe_overhead(self, phase, elapsed):
        with self.lock:
            record = self.overhead.setdefault(phase, {"count": 0, "total_ns": 0, "max_ns": 0})
            record["count"] += 1
            record["total_ns"] += elapsed
            record["max_ns"] = max(record["max_ns"], elapsed)

    def alias(self, pid):
        with self.lock:
            if pid == 0:
                return 0  # PostgreSQL can use zero for a prepared-transaction blocker.
            if pid not in self.aliases:
                if len(self.aliases) >= 64:
                    raise ValueError("connection alias bound")
                self.aliases[pid] = len(self.aliases) + 1
            return self.aliases[pid]

    @contextmanager
    def operation(self, name):
        previous = getattr(self.local, "operation", None)
        self.local.operation = name if name in {"governance", "ai_connection", "export"} else "other"
        start, cpu = self.now(), time.thread_time_ns()
        usage = resource.getrusage(resource.RUSAGE_THREAD)
        try:
            yield
        finally:
            end = resource.getrusage(resource.RUSAGE_THREAD)
            if self.local.operation != "other":
                self.add("operations", {"lane": self.local.operation, "index": getattr(self.local, "index", -1), "start_ns": start, "end_ns": self.now(), "thread_cpu_ns": time.thread_time_ns() - cpu, "voluntary_switches": end.ru_nvcsw - usage.ru_nvcsw, "involuntary_switches": end.ru_nivcsw - usage.ru_nivcsw})
            self.local.operation = previous

    def lane(self, original, operation, **kwargs):
        delay, interval = kwargs.get("initial_delay_seconds", 0), kwargs["interval_seconds"]
        name = "governance" if delay == 0.5 else "ai_connection" if delay == 0.25 else "export" if interval == 2 else "feed" if interval == 10 else "repair" if interval == 5 else "other"
        self.add("lanes", {"lane": name, "start_ns": self.now(), "duration_seconds": kwargs["duration_seconds"], "interval_seconds": interval, "initial_delay_seconds": delay})

        def observed(index):
            self.local.index = index
            return operation(index)

        return original(observed, **kwargs)

    def query_start(self, connection, statement):
        category = classify_sql(statement)
        if category == "other":
            return None
        return {"category": category, "connection_id": self.alias(backend_pid(connection)), "lane": getattr(self.local, "operation", None) or "background", "index": getattr(self.local, "index", -1), "start_ns": self.now()}

    def query_end(self, token, *, succeeded=True):
        if token is None:
            return
        token["end_ns"] = self.now()
        token["status"] = "succeeded" if succeeded else "failed"
        with self.lock:
            if succeeded and token["category"] in {"policy_exclusive", "policy_shared", "iam_shared", "label_exclusive"}:
                self.fences.setdefault(token["connection_id"], set()).add(token["category"])
        self.add("queries", token)

    def transaction(self, connection, event):
        alias = self.alias(backend_pid(connection))
        with self.lock:
            interesting = bool(self.fences.get(alias))
            if event in {"pool_returned", "begin"}:
                self.fences.pop(alias, None)
                self.ending.discard(alias)
            elif interesting:
                self.ending.add(alias)
        if interesting:
            self.add("transactions", {"connection_id": alias, "event": event, "at_ns": self.now()})

    def sampler(self, cursor):
        now = self.now()
        if self.last_sampler is not None:
            gap = now - self.last_sampler
            self.sampler_gap_sum += gap
            self.sampler_gap_max = max(self.sampler_gap_max, gap)
        self.last_sampler = now
        self.sampler_count += 1
        if now - self.last_lock_sample >= 100_000_000:
            self.last_lock_sample = now
            observer, started = None, self.now()
            try:
                # A distinct DBAPI cursor never consumes the original result,
                # starts a new application transaction or invokes engine hooks.
                observer = cursor.connection.cursor()
                observer.execute(WAIT_SQL)
                rows = observer.fetchall()
                if len(rows) > 64:
                    raise ValueError("waiter/blocker row bound")
                with self.lock:
                    fences = {alias: sorted(categories) for alias, categories in self.fences.items()}
                    ending = set(self.ending)
                values = []
                for pid, statement, wait, blockers, query_age, transaction_age in rows:
                    alias = self.alias(pid)
                    if len(blockers or []) > 16:
                        raise ValueError("blocker bound")
                    values.append({"connection_id": alias, "category": classify_sql(statement or ""), "wait_category": wait.lower() if (wait or "").lower() in {"transactionid", "tuple", "relation", "advisory", "extend"} else "other", "blocker_ids": [self.alias(value) for value in blockers or []], "is_waiter": blockers is not None, "query_age_ms": float(query_age or 0), "transaction_age_ms": float(transaction_age or 0), "observed_fences": fences.get(alias, []), "transaction_end_requested": alias in ending})
                self.add("locks", {"sample_ns": now, "observer_elapsed_ns": self.now() - started, "connections": values})
            except Exception as error:
                self.error("waiter_observer", error)
            finally:
                if observer is not None:
                    try:
                        observer.close()
                    except Exception as error:
                        self.error("waiter_cursor_close", error)
        if now - self.last_host_sample >= 1_000_000_000:
            self.last_host_sample = now
            try:
                self.add("host", host_sample(now))
            except Exception as error:
                self.error("host_observer", error)

    def finish(self, expected_governance):
        counts = {name: len(values) for name, values in self.data.items()}
        counts["governance_operations"] = sum(value["lane"] == "governance" for value in self.data["operations"])
        coverage = {"governance_operations": counts["governance_operations"], "operation_events": counts["operations"], "query_events": counts["queries"], "lock_samples": counts["locks"], "host_samples": counts["host"], "lane_starts": counts["lanes"]}
        lanes = [value["lane"] for value in self.data["lanes"]]
        if expected_governance <= 0 or coverage["governance_operations"] != expected_governance or any(not coverage[key] for key in ["query_events", "lock_samples", "host_samples"]) or sorted(lanes) != ["ai_connection", "export", "feed", "governance", "repair"]:
            self.error("coverage", ValueError())
        return {"status": "failed" if self.errors or any(self.dropped.values()) else "passed", "errors": self.errors, "dropped_events": self.dropped, "coverage": coverage, "observer_overhead": self.overhead, "sampler": {"observations": self.sampler_count, "gap_max_ns": self.sampler_gap_max, "gap_mean_ns": self.sampler_gap_sum // max(1, self.sampler_count - 1)}, "connection_alias_count": len(self.aliases), "lock_semantics": "Sampled query age while Lock-waiting; successful fence observations retire at pool return or next begin; transaction-end requests are not exact release times", **self.data}


def host_sample(now):
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = {"sample_ns": now, "process_cpu_ns": time.process_time_ns(), "voluntary_switches": usage.ru_nvcsw, "involuntary_switches": usage.ru_nivcsw, "load_average": list(os.getloadavg())}
    result["pressure"] = {}
    for kind in ["cpu", "io", "memory"]:
        path = Path("/proc/pressure") / kind
        result["pressure"][kind] = {"available": path.exists()}
        if path.exists():
            result["pressure"][kind]["totals_us"] = [int(match) for match in re.findall(r"\btotal=(\d+)", path.read_text())]
    group = next((line.split(":", 2)[2] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::")), None)
    result["cgroup_cpu"] = {"available": False}
    if group is not None and ".." not in Path(group).parts:
        path = Path("/sys/fs/cgroup") / group.lstrip("/") / "cpu.stat"
        if path.exists():
            result["cgroup_cpu"] = {"available": True, **{key: int(value) for key, value in (line.split() for line in path.read_text().splitlines()) if key in {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec"}}}
    result["affinity_cpu"] = {"available": False}
    path = Path("/proc/stat")
    if path.exists():
        selected = set(sorted(os.sched_getaffinity(0))[:4])
        cpus = []
        for line in path.read_text().splitlines():
            columns = line.split()
            if columns and re.fullmatch(r"cpu\d+", columns[0]) and int(columns[0][3:]) in selected:
                counters = [int(value) for value in columns[1:]]
                cpus.append({"cpu_index": int(columns[0][3:]), **dict(zip(["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"], counters[:8]))})
        result["affinity_cpu"] = {"available": bool(cpus), "counters_in_clock_ticks": True, "cpus": cpus}
    return result

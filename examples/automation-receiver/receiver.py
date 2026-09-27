#!/usr/bin/env python3
"""Vendor-neutral receiver ledger: durable acceptance, status replay and withdrawal.

The serve/sync modes are vendor-neutral. The explicit opensearch-sync mode runs
approved indicator hunts using a durable remote action ledger. Neither mode turns
an ambiguous launch into a second launch.

For sync, THREATLENS_URL is the API base before /v1: use
https://threatlens.example/api with the bundled web proxy, or the direct backend
origin without /api. The client appends /v1. Set THREATLENS_API_TOKEN separately.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import signal
import sqlite3
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
import uuid

MAX_BODY = 280_000
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 3600
SYNC_BATCH_SIZE = 100


@contextmanager
def absolute_deadline(seconds: float):
    """POSIX main-thread bound covering DNS, headers and slowly streamed bodies."""
    if (
        not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        raise ValueError("The reference receiver requires a POSIX main-thread runtime")
    if seconds <= 0:
        raise TimeoutError("Receiver synchronization deadline exceeded")
    if any(signal.getitimer(signal.ITIMER_REAL)):
        raise ValueError("The reference receiver cannot share an active process alarm")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum, _frame):
        raise TimeoutError("Receiver transfer deadline exceeded")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class Ledger:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        if path != ":memory:":
            os.chmod(path, 0o600)
        self.policy_gate = None
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS jobs (
            execution_id TEXT PRIMARY KEY, webhook_id TEXT NOT NULL,
            action_id TEXT NOT NULL, body_hash TEXT NOT NULL, job_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, callback TEXT NOT NULL, pending INTEGER NOT NULL,
            withdrawn INTEGER NOT NULL DEFAULT 0,
            UNIQUE(webhook_id, action_id)
          );
          CREATE TABLE IF NOT EXISTS policy_receipts (
            id TEXT PRIMARY KEY, digest TEXT NOT NULL, execution_id TEXT NOT NULL,
            acknowledged INTEGER NOT NULL DEFAULT 0
          );
        """)
        # Additive upgrades preserve ledgers from earlier receiver versions.
        for table in ("jobs", "policy_receipts"):
            columns = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for name, definition in (
                ("attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("next_attempt_at", "REAL NOT NULL DEFAULT 0"),
                ("last_attempt_at", "REAL NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        for table, name, definition in (
            ("jobs", "body", "TEXT"),
            ("jobs", "vendor_next_at", "REAL NOT NULL DEFAULT 0"),
            ("jobs", "vendor_last_at", "REAL NOT NULL DEFAULT 0"),
            ("policy_receipts", "payload", "TEXT"),
        ):
            if name not in {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        self.db.execute("CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_retry ON jobs(pending, next_attempt_at, last_attempt_at)")
        self.db.execute("CREATE INDEX IF NOT EXISTS policy_retry ON policy_receipts(acknowledged, next_attempt_at, last_attempt_at)")
        self.db.commit()

    def reserve_attempt(self, table: str, identity: str, *, now: float) -> None:
        """Persist backoff before I/O, including crashes after remote acceptance."""
        if table not in {"jobs", "policy_receipts"}:
            raise ValueError("Unsupported outbox")
        key = "execution_id" if table == "jobs" else "id"
        with self.db:
            attempts = self.db.execute(
                f"SELECT attempts FROM {table} WHERE {key}=?", (identity,)
            ).fetchone()[0]
            delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2 ** min(attempts, 10))
            jitter = int(hashlib.sha256(identity.encode()).hexdigest()[:4], 16) / 65535
            self.db.execute(
                f"UPDATE {table} SET attempts=attempts+1, next_attempt_at=?, last_attempt_at=? WHERE {key}=?",
                (now + delay * (1 + jitter / 4), now, identity),
            )

    def accept(self, body: bytes) -> dict:
        envelope = json.loads(body)
        if envelope.get("schema_version") != "threatlens.automation.v1":
            raise ValueError("Unsupported automation schema")
        data = envelope["data"]
        execution = data["execution"]
        execution_id = str(uuid.UUID(execution["id"]))
        webhook_id = str(uuid.UUID(execution["webhook_id"]))
        action_id = str(envelope["action_id"])
        digest = hashlib.sha256(body).hexdigest()
        with self.db:
            existing = self.db.execute(
                "SELECT body_hash, callback FROM jobs WHERE webhook_id=? AND action_id=?",
                (webhook_id, action_id),
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError("The action ID was reused for different content")
                return json.loads(existing[1])
            if self.db.execute(
                "SELECT 1 FROM policy_receipts WHERE execution_id=? LIMIT 1",
                (execution_id,),
            ).fetchone():
                raise ValueError(
                    "This action was withdrawn before delivery; do not launch it"
                )
            # Acceptance is durable before HTTP 202. The stable local job represents
            # queued review; this is not an assertion that a SIEM hunt has run.
            callback = {
                "callback_id": str(uuid.uuid4()),
                "sequence": 1,
                "external_job_id": "tl-action:" + hashlib.sha256(f"{webhook_id}:{action_id}".encode()).hexdigest(),
                "status": "accepted",
                "findings": None,
            }
            self.db.execute(
                "INSERT INTO jobs (execution_id, webhook_id, action_id, body_hash, job_id, sequence, callback, pending, withdrawn, body) VALUES (?, ?, ?, ?, ?, 1, ?, 1, 0, ?)",
                (
                    execution_id,
                    webhook_id,
                    action_id,
                    digest,
                    callback["external_job_id"],
                    json.dumps(callback),
                    body.decode("utf-8"),
                ),
            )
        return callback

    def status(self, execution_id: str, status: str, findings: str | None = None):
        if status not in {"running", "completed", "failed", "unknown"}:
            raise ValueError("Unsupported receiver status")
        if findings is not None and (
            status != "completed" or len(findings) > 8000 or "\x00" in findings
        ):
            raise ValueError("Findings must be bounded completed output")
        with self.db:
            row = self.db.execute(
                "SELECT job_id, sequence, callback, withdrawn, pending FROM jobs WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown execution")
            if row[4]:
                raise ValueError(
                    "Deliver the previous callback before advancing status"
                )
            if json.loads(row[2])["status"] in {"completed", "failed"}:
                raise ValueError("Terminal status is immutable")
            if row[3] and status == "running":
                raise ValueError("Withdrawn intelligence must not start a new hunt")
            callback = {
                "callback_id": str(uuid.uuid4()),
                "sequence": row[1] + 1,
                "external_job_id": row[0],
                "status": status,
                "findings": findings,
            }
            self.db.execute(
                "UPDATE jobs SET sequence=?, callback=?, pending=1, attempts=0, next_attempt_at=0, last_attempt_at=0 WHERE execution_id=?",
                (callback["sequence"], json.dumps(callback), execution_id),
            )

    def apply_policy(self, update: dict):
        digest = hashlib.sha256(json.dumps(update, sort_keys=True).encode()).hexdigest()
        with self.db:
            existing = self.db.execute(
                "SELECT digest FROM policy_receipts WHERE id=?", (update["id"],)
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError(
                        "Policy update ID was reused for different content"
                    )
                return
            if update["event_type"] not in {"intel.withdrawn", "intel.replaced"}:
                raise ValueError("Unsupported policy event")
            self.db.execute(
                "UPDATE jobs SET withdrawn=1 WHERE execution_id=?",
                (update["execution_id"],),
            )
            self.db.execute(
                "INSERT INTO policy_receipts (id, digest, execution_id, acknowledged, payload) VALUES (?, ?, ?, 0, ?)",
                (update["id"], digest, update["execution_id"], json.dumps(update)),
            )
            # Completed job and findings remain immutable history. A replacement
            # action must arrive through normal authorization/approval separately.


def api(base: str, token: str, path: str, payload=None, *, timeout_seconds: float = 15):
    parsed = urlsplit(base)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
    ):
        raise ValueError(
            "Use an absolute server URL without credentials, query or fragment"
        )
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    ):
        raise ValueError(
            "Use HTTPS for ThreatLens, or explicit loopback for local tests"
        )
    if token.startswith("tlrecv_"):
        path = path.replace("/notifications/automation/", "/notifications/automation/receivers/", 1)
    request = Request(
        base.rstrip("/") + "/v1" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            raise ValueError("Receiver API redirects are not permitted")

    with (
        absolute_deadline(min(timeout_seconds, 15)),
        build_opener(NoRedirect()).open(
            request, timeout=min(timeout_seconds, 15)
        ) as response,
    ):
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError("Response exceeded receiver limit")
        return json.loads(body)


def synchronize(ledger: Ledger, request):
    """Each outbox receives a time slice; failed rows yield to untouched work."""
    failures = []
    try:
        synchronize_policy(ledger, request)
    except (OSError, ValueError) as exc:
        failures.append(exc)
    stop_at = time.monotonic() + 20
    rows = ledger.db.execute(
        "SELECT execution_id, callback FROM jobs WHERE pending=1 AND next_attempt_at<=? "
        "ORDER BY last_attempt_at, execution_id LIMIT ?", (time.time(), SYNC_BATCH_SIZE)
    ).fetchall()
    for identity, callback in rows:
        if time.monotonic() >= stop_at:
            break
        ledger.reserve_attempt("jobs", identity, now=time.time())
        try:
            request(
                f"/notifications/automation/executions/{identity}/callbacks",
                json.loads(callback),
            )
        except (OSError, ValueError) as exc:
            failures.append(exc)
            continue
        with ledger.db:
            ledger.db.execute(
                "UPDATE jobs SET pending=0 WHERE execution_id=? AND callback=?",
                (identity, callback),
            )
    if failures:
        raise failures[0]


def synchronize_policy(ledger: Ledger, request):
    failures = []
    # Traversal is independent from ACK success; wrap to discover new lower IDs.
    cursor = ledger.db.execute("SELECT value FROM sync_state WHERE key='policy_cursor'").fetchone()
    suffix = f"&after={str(uuid.UUID(cursor[0]))}" if cursor and cursor[0] else ""
    try:
        page = request(f"/notifications/automation/updates?limit=100{suffix}")
        for update in page["items"]:
            ledger.apply_policy(update)
        next_cursor = page.get("next_cursor")
        if next_cursor:
            next_cursor = str(uuid.UUID(next_cursor))
        with ledger.db:
            ledger.db.execute(
                "INSERT INTO sync_state VALUES ('policy_cursor', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (next_cursor,),
            )
    except (OSError, ValueError) as exc:
        failures.append(exc)
    stop_at = time.monotonic() + 15
    pending = ledger.db.execute(
        "SELECT id FROM policy_receipts WHERE acknowledged=0 AND next_attempt_at<=? "
        "ORDER BY last_attempt_at, id LIMIT ?", (time.time(), SYNC_BATCH_SIZE)
    ).fetchall()
    for (identity,) in pending:
        if time.monotonic() >= stop_at:
            break
        ledger.reserve_attempt("policy_receipts", identity, now=time.time())
        try:
            if ledger.policy_gate is not None:
                ledger.policy_gate(identity)
            request(f"/notifications/automation/updates/{identity}/ack", {})
        except (OSError, ValueError) as exc:
            failures.append(exc)
            continue
        with ledger.db:
            ledger.db.execute(
                "UPDATE policy_receipts SET acknowledged=1 WHERE id=?", (identity,)
            )
    if failures:
        raise failures[0]


def verify_signature(body: bytes, headers, secret: str):
    stamp = headers.get("X-ThreatLens-Timestamp", "")
    if (
        not stamp.isascii()
        or not stamp.isdecimal()
        or abs(time.time() - int(stamp)) > 300
    ):
        raise ValueError(
            "Signature timestamp is absent or outside the five-minute window"
        )
    event, attempt = (
        headers.get("X-ThreatLens-Event-ID", ""),
        headers.get("X-ThreatLens-Attempt-ID", ""),
    )
    uuid.UUID(event)
    uuid.UUID(attempt)
    canonical = (
        b"v1\n"
        + stamp.encode()
        + b"\n"
        + event.encode()
        + b"\n"
        + attempt.encode()
        + b"\n"
        + body
    )
    expected = "v1=" + hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, headers.get("X-ThreatLens-Signature", "")):
        raise ValueError("Signature mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["serve", "sync", "status", "opensearch-sync", "opensearch-bind"])
    parser.add_argument("--database", default="receiver.sqlite3")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--execution-id")
    parser.add_argument(
        "--status", choices=["running", "completed", "failed", "unknown"]
    )
    parser.add_argument("--findings")
    parser.add_argument("--external-search-id")
    parser.add_argument("--confirm-query-digest")
    args = parser.parse_args()
    ledger = Ledger(args.database)
    if args.mode in {"sync", "opensearch-sync"}:
        deadline = time.monotonic() + 60

        def request(path, payload=None):
            return api(
                os.environ["THREATLENS_URL"],
                os.environ["THREATLENS_API_TOKEN"],
                path,
                payload,
                timeout_seconds=deadline - time.monotonic(),
            )

        if args.mode == "opensearch-sync":
            from opensearch_connector import configured_connector
            from opensearch_runner import run_connector
            connector = configured_connector(lambda seconds: absolute_deadline(min(seconds, deadline - time.monotonic())))
            run_connector(ledger, request, connector, synchronize)
        else:
            synchronize(ledger, request)
    elif args.mode == "opensearch-bind":
        from opensearch_connector import action_identity, configured_connector
        job = ledger.db.execute("SELECT body FROM jobs WHERE execution_id=?", (args.execution_id,)).fetchone()
        if not job or not job[0] or not args.external_search_id or not args.confirm_query_digest:
            raise ValueError("Binding requires a known execution, confirmed query digest and external search ID")
        configured_connector(absolute_deadline).bind(action_identity(json.loads(job[0])), args.external_search_id, args.confirm_query_digest)
    elif args.mode == "status":
        ledger.status(args.execution_id, args.status, args.findings)
    else:
        secret = os.environ["THREATLENS_SIGNING_SECRET"]
        if len(secret) < 32:
            raise ValueError("Use a signing secret of at least 32 characters")

        # Fail before binding if the runtime cannot enforce absolute deadlines.
        with absolute_deadline(1):
            pass

        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                self.connection.settimeout(15)
                try:
                    with absolute_deadline(15):
                        super().handle()
                except (OSError, ValueError):
                    self.close_connection = True

            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if (
                        size < 1
                        or size > MAX_BODY
                        or self.headers.get("Transfer-Encoding")
                    ):
                        raise ValueError(
                            "Unsupported request size or transfer encoding"
                        )
                    self.connection.settimeout(15)
                    body = self.rfile.read(size)
                    verify_signature(body, self.headers, secret)
                    result = ledger.accept(body)
                    response = json.dumps(result).encode()
                    self.send_response(202)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except (ValueError, KeyError, TypeError, RecursionError):
                    self.send_error(400, "Invalid signed automation request")

            def log_message(self, *_args):
                pass  # Do not log signed payloads or credentials.

        HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        raise SystemExit(
            f"Receiver operation failed ({type(error).__name__}); verify runtime, configuration and connectivity."
        ) from None

#!/usr/bin/env python3
"""Vendor-neutral receiver ledger: durable acceptance, status replay and withdrawal.

This example deliberately does not invoke a SIEM. Integrate a vendor adapter by
looking up the existing remote action ID before launch, then record its job ID.
Never turn an ambiguous launch into a second launch.
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
                "external_job_id": str(uuid.uuid4()),
                "status": "accepted",
                "findings": None,
            }
            self.db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, 1, ?, 1, 0)",
                (
                    execution_id,
                    webhook_id,
                    action_id,
                    digest,
                    callback["external_job_id"],
                    json.dumps(callback),
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
                "UPDATE jobs SET sequence=?, callback=?, pending=1 WHERE execution_id=?",
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
                "INSERT INTO policy_receipts VALUES (?, ?, ?, 0)",
                (update["id"], digest, update["execution_id"]),
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
    """Drain a bounded receipt batch without one failing job starving withdrawals."""
    failures = []
    # Apply safety withdrawals before status uploads; slow callbacks must not
    # consume the entire synchronization budget before control records are read.
    try:
        synchronize_policy(ledger, request)
    except (OSError, ValueError) as exc:
        failures.append(exc)
    rows = ledger.db.execute(
        "SELECT execution_id, callback FROM jobs WHERE pending=1 ORDER BY execution_id LIMIT 100"
    ).fetchall()
    for identity, callback in rows:
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
    # A receiver can crash after applying the update, or after the server accepted
    # its ACK. Resume local receipts first; GET only returns unacknowledged rows.
    pending = ledger.db.execute(
        "SELECT id FROM policy_receipts WHERE acknowledged=0 ORDER BY id LIMIT 100"
    ).fetchall()
    for (identity,) in pending:
        try:
            request(f"/notifications/automation/updates/{identity}/ack", {})
        except (OSError, ValueError) as exc:
            failures.append(exc)
            continue
        with ledger.db:
            ledger.db.execute(
                "UPDATE policy_receipts SET acknowledged=1 WHERE id=?", (identity,)
            )
    page = request("/notifications/automation/updates?limit=100")
    for update in page["items"]:
        try:
            ledger.apply_policy(update)
            request(f"/notifications/automation/updates/{update['id']}/ack", {})
        except (OSError, ValueError) as exc:
            failures.append(exc)
            continue
        with ledger.db:
            ledger.db.execute(
                "UPDATE policy_receipts SET acknowledged=1 WHERE id=?", (update["id"],)
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
    parser.add_argument("mode", choices=["serve", "sync", "status"])
    parser.add_argument("--database", default="receiver.sqlite3")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--execution-id")
    parser.add_argument(
        "--status", choices=["running", "completed", "failed", "unknown"]
    )
    parser.add_argument("--findings")
    args = parser.parse_args()
    ledger = Ledger(args.database)
    if args.mode == "sync":
        deadline = time.monotonic() + 60

        def request(path, payload=None):
            return api(
                os.environ["THREATLENS_URL"],
                os.environ["THREATLENS_API_TOKEN"],
                path,
                payload,
                timeout_seconds=deadline - time.monotonic(),
            )

        synchronize(ledger, request)
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

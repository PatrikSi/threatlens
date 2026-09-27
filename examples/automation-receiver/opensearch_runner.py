"""Adapt durable receiver acceptance/status/withdrawals to OpenSearch searches."""

import json
import time


def run_connector(ledger, request, connector, synchronize):
    def policy_gate(identity: str):
        row = ledger.db.execute(
            "SELECT execution_id, payload FROM policy_receipts WHERE id=?", (identity,)
        ).fetchone()
        job = ledger.db.execute(
            "SELECT body FROM jobs WHERE execution_id=?", (row[0],)
        ).fetchone()
        if job and job[0]:
            envelope = json.loads(job[0])
        else:
            update = json.loads(row[1] or "{}")
            if not update.get("action_id") or not update.get("webhook_id"):
                raise ValueError(
                    "Legacy withdrawal needs operator reconciliation before using this connector"
                )
            envelope = {
                "action_id": update["action_id"],
                "data": {
                    "execution": {"id": row[0], "webhook_id": update["webhook_id"]}
                },
            }
        connector.withdraw(envelope)

    ledger.policy_gate = policy_gate
    # A failed withdrawal or unavailable policy feed must stop new launches.
    synchronize(ledger, request)
    cursor = ledger.db.execute(
        "SELECT value FROM sync_state WHERE key='policy_cursor'"
    ).fetchone()
    if cursor and cursor[0]:
        return  # Finish the bounded control-feed traversal before any launch.
    if ledger.db.execute(
        "SELECT 1 FROM policy_receipts WHERE acknowledged=0 LIMIT 1"
    ).fetchone():
        return
    cancelled = ledger.db.execute(
        "SELECT execution_id FROM jobs WHERE pending=0 AND withdrawn=1 "
        "AND json_extract(callback, '$.status') NOT IN ('completed', 'failed') "
        "AND NOT EXISTS (SELECT 1 FROM policy_receipts p WHERE p.execution_id=jobs.execution_id AND p.acknowledged=0) "
        "ORDER BY execution_id LIMIT 3"
    ).fetchall()
    for (identity,) in cancelled:
        ledger.status(identity, "failed")
    rows = ledger.db.execute(
        "SELECT execution_id, body, callback FROM jobs WHERE pending=0 AND withdrawn=0 AND body IS NOT NULL AND vendor_next_at<=? "
        "AND json_extract(callback, '$.status') NOT IN ('completed', 'failed') ORDER BY vendor_last_at, execution_id LIMIT 3",
        (time.time(),),
    ).fetchall()
    for identity, body, previous in rows:
        now = time.time()
        with ledger.db:
            ledger.db.execute(
                "UPDATE jobs SET vendor_next_at=?, vendor_last_at=? WHERE execution_id=?",
                (now + 30, now, identity),
            )
        try:
            status, findings = connector.advance(json.loads(body))
        except (OSError, ValueError):
            status, findings = "unknown", None
        if json.loads(previous)["status"] != status:
            ledger.status(identity, status, findings)
    synchronize(ledger, request)

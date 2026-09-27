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
    # Policy failures stop vendor work. Unrelated callback failures remain visible
    # after the vendor lane has received its independent bounded time slice.
    callback_errors = []
    synchronize(ledger, request, callback_errors=callback_errors, callback_budget_seconds=5)
    cursor = ledger.db.execute(
        "SELECT value FROM sync_state WHERE key='policy_cursor'"
    ).fetchone()
    unacknowledged = ledger.db.execute(
        "SELECT 1 FROM policy_receipts WHERE acknowledged=0 LIMIT 1"
    ).fetchone()
    if not (cursor and cursor[0]) and not unacknowledged:
        _advance_ready_jobs(ledger, request, connector)
        synchronize(ledger, request, callback_errors=callback_errors, callback_budget_seconds=5)
    if callback_errors:
        raise callback_errors[0]


def _policy_head_is_clear(request) -> bool:
    """An old UUID cursor finishing is not proof that the control feed is empty.

    Read the current pending head before every possible launch. Any outstanding
    update defers work to the bounded traversal; never drain unbounded pages here.
    """
    page = request("/notifications/automation/updates?limit=1")
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        raise ValueError("Invalid policy response; vendor work remains deferred")
    if len(page["items"]) > 1:
        raise ValueError("Policy response exceeded the requested limit")
    return not page["items"] and not page.get("next_cursor")


def _advance_ready_jobs(ledger, request, connector):
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
        if not _policy_head_is_clear(request):
            return
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

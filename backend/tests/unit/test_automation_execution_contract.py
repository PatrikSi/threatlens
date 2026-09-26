import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from app.schemas.automation_execution import ExecutionCallback
from app.services.automation_executions import apply_callback


class MemoryDB:
    def __init__(self):
        self.receipts = {}

    def get(self, _model, key):
        return self.receipts.get(key)

    def add(self, receipt):
        self.receipts[(receipt.execution_id, receipt.callback_id)] = receipt


def row():
    return SimpleNamespace(
        id=uuid.uuid4(),
        sequence=0,
        external_job_id=None,
        status="unknown",
        progress_stage=0,
    )


def callback(sequence=1, status="accepted", **kwargs):
    return ExecutionCallback(
        callback_id=uuid.uuid4(),
        sequence=sequence,
        external_job_id=kwargs.pop("external_job_id", "job-1"),
        status=status,
        **kwargs,
    )


def test_duplicate_callback_is_idempotent_but_conflicting_reuse_is_rejected():
    db, execution = MemoryDB(), row()
    original = callback()
    assert apply_callback(db, execution, original)
    assert not apply_callback(db, execution, original)
    with pytest.raises(HTTPException) as error:
        apply_callback(db, execution, original.model_copy(update={"status": "running"}))
    assert error.value.status_code == 409


@pytest.mark.parametrize("bad", ["bad\x00text", "bad\ud800text", "  "])
def test_storage_unsafe_callback_rejected(bad):
    with pytest.raises(ValidationError):
        callback(external_job_id=bad)


def test_unknown_does_not_erase_running_progress_and_terminal_is_immutable():
    db, execution = MemoryDB(), row()
    apply_callback(db, execution, callback(status="running"))
    apply_callback(db, execution, callback(2, "unknown"))
    with pytest.raises(HTTPException):
        apply_callback(db, execution, callback(3, "accepted"))
    final = callback(4, "completed", findings="One reviewed result")
    apply_callback(db, execution, final)
    assert not apply_callback(db, execution, final)
    with pytest.raises(HTTPException):
        apply_callback(db, execution, callback(5, "failed"))
    assert execution.findings == "One reviewed result"


def test_out_of_order_and_job_reassignment_rejected():
    db, execution = MemoryDB(), row()
    apply_callback(db, execution, callback(2, "running"))
    for next_callback in (
        callback(1),
        callback(2, "running"),
        callback(3).model_copy(update={"external_job_id": "other"}),
    ):
        with pytest.raises(HTTPException) as error:
            apply_callback(db, execution, next_callback)
        assert error.value.status_code == 409


def receiver_module():
    path = (
        Path(__file__).resolve().parents[3] / "examples/automation-receiver/receiver.py"
    )
    spec = importlib.util.spec_from_file_location("reference_receiver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def receiver_body():
    return json.dumps(
        {
            "schema_version": "threatlens.automation.v1",
            "action_id": "stable-action",
            "data": {
                "execution": {"id": str(uuid.uuid4()), "webhook_id": str(uuid.uuid4())},
            },
        }
    ).encode()


def test_receiver_crash_after_accept_reuses_job_and_replays_lost_callback(tmp_path):
    receiver = receiver_module()
    path = str(tmp_path / "receiver.sqlite3")
    body = receiver_body()
    first = receiver.Ledger(path)
    accepted = first.accept(body)
    first.db.close()  # HTTP response was lost before callback transmission.
    restarted = receiver.Ledger(path)
    assert restarted.accept(body) == accepted
    sent = []

    def lost_response(path, payload=None):
        sent.append((path, payload))
        raise OSError("reply lost after application committed callback")

    with pytest.raises(OSError):
        receiver.synchronize(restarted, lost_response)

    def successful(path, payload=None):
        if payload:
            assert payload == accepted
        return {"items": []}

    receiver.synchronize(restarted, successful)
    assert restarted.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert restarted.db.execute("SELECT pending FROM jobs").fetchone()[0] == 0


def test_receiver_withdrawal_is_idempotent_and_keeps_completed_results(tmp_path):
    receiver = receiver_module()
    ledger = receiver.Ledger(str(tmp_path / "ledger.db"))
    body = receiver_body()
    execution = json.loads(body)["data"]["execution"]["id"]
    ledger.accept(body)
    receiver.synchronize(ledger, lambda *args: {"items": []})
    ledger.status(execution, "completed", "Retained evidence")
    update = {
        "id": str(uuid.uuid4()),
        "execution_id": execution,
        "event_type": "intel.withdrawn",
        "revision": 1,
    }
    ledger.apply_policy(update)
    ledger.apply_policy(update)
    result = ledger.db.execute("SELECT callback, withdrawn FROM jobs").fetchone()
    assert json.loads(result[0])["findings"] == "Retained evidence"
    assert result[1] == 1
    with pytest.raises(ValueError):
        ledger.apply_policy({**update, "revision": 2})


def test_receiver_rejects_conflicting_same_action_body(tmp_path):
    receiver = receiver_module()
    ledger = receiver.Ledger(str(tmp_path / "ledger.db"))
    body = receiver_body()
    ledger.accept(body)
    with pytest.raises(ValueError):
        ledger.accept(body + b" ")


def test_policy_tombstone_prevents_late_delivery_from_starting_job(tmp_path):
    receiver = receiver_module()
    ledger = receiver.Ledger(str(tmp_path / "ledger.db"))
    body = receiver_body()
    execution_id = json.loads(body)["data"]["execution"]["id"]
    ledger.apply_policy(
        {
            "id": str(uuid.uuid4()),
            "execution_id": execution_id,
            "event_type": "intel.withdrawn",
            "revision": 1,
        }
    )
    with pytest.raises(ValueError, match="withdrawn"):
        ledger.accept(body)
    assert ledger.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_callback_failure_does_not_starve_policy_receipts(tmp_path):
    receiver = receiver_module()
    ledger = receiver.Ledger(str(tmp_path / "ledger.db"))
    body = receiver_body()
    execution_id = json.loads(body)["data"]["execution"]["id"]
    ledger.accept(body)
    update = {
        "id": str(uuid.uuid4()),
        "execution_id": execution_id,
        "event_type": "intel.withdrawn",
        "revision": 1,
    }

    def request(path, payload=None):
        if path.endswith("/callbacks"):
            raise OSError("Callback unavailable")
        return {"items": [update]} if payload is None else {}

    with pytest.raises(OSError):
        receiver.synchronize(ledger, request)
    assert ledger.db.execute("SELECT withdrawn FROM jobs").fetchone()[0] == 1
    assert (
        ledger.db.execute("SELECT acknowledged FROM policy_receipts").fetchone()[0] == 1
    )


def test_request_signature_is_verified_over_exact_bytes():
    from app.services.webhook_credentials import signed_headers

    receiver = receiver_module()
    body = receiver_body()
    secret = "reference-receiver-secret-32-characters"
    headers = signed_headers(
        secret=secret,
        body=body,
        event_id=str(uuid.uuid4()),
        attempt_id=str(uuid.uuid4()),
    )
    receiver.verify_signature(body, headers, secret)
    with pytest.raises(ValueError, match="mismatch"):
        receiver.verify_signature(body + b" ", headers, secret)
    stale = signed_headers(
        secret=secret,
        body=body,
        event_id=str(uuid.uuid4()),
        attempt_id=str(uuid.uuid4()),
        timestamp=0,
    )
    with pytest.raises(ValueError, match="window"):
        receiver.verify_signature(body, stale, secret)


def test_receiver_replays_policy_ack_after_server_already_removed_pending_row(tmp_path):
    receiver = receiver_module()
    ledger = receiver.Ledger(str(tmp_path / "ledger.db"))
    update = {
        "id": str(uuid.uuid4()),
        "execution_id": str(uuid.uuid4()),
        "event_type": "intel.withdrawn",
        "revision": 1,
    }
    ledger.apply_policy(update)
    acknowledgements = []

    def recovered(path, payload=None):
        if payload is not None:
            acknowledgements.append(path)
            return {}
        return {"items": []}  # Server accepted the previous ACK before the crash.

    receiver.synchronize_policy(ledger, recovered)
    assert acknowledgements == [f"/notifications/automation/updates/{update['id']}/ack"]
    assert (
        ledger.db.execute("SELECT acknowledged FROM policy_receipts").fetchone()[0] == 1
    )

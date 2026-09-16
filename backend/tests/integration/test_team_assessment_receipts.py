"""Receipt replay safety follows a team's assessment, not the shared article."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.team_item_assessment import TeamItemAssessment
from app.services.action_approval_data_policy import (
    resolve_registered_action_target_data_access,
)
from app.services.action_registry import get_registered_action
from app.services.ai_provider_attempts import (
    AIProviderTaskBindingError,
    lock_ai_provider_attempt_for_io,
    reserve_ai_provider_attempt,
    settle_ai_provider_attempt,
)
from app.services.audit_data_access import resolve_audit_data_access_labels
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    get_data_access_envelope,
)
from tests.integration.test_ai_telemetry_data_policy import _context
from tests.integration.test_team_assessment_worker import (
    _completion,
    accepted_assessment as accepted_assessment,
    invoke_worker,
)
from tests.integration.test_teams_api import _team


def _reserve(db, run, *, item_id):
    return reserve_ai_provider_attempt(
        db,
        task_run_id=run.id,
        feature_type="team_assessment",
        item_id=item_id,
        daily_brief_id=None,
        report_id=None,
        operation_scope="initial",
        attempt_number=1,
        max_attempts=1,
        requested_max_tokens=1024,
        request_fingerprint="a" * 64,
        iam_revision=1,
        data_policy_revision=1,
        data_policy_mode="disabled",
    )


@pytest.mark.parametrize("receipt_state", ["reserved", "ambiguous"])
def test_unresolved_receipt_blocks_same_assessment_but_not_another_team(
    client,
    db_session,
    accepted_assessment,
    seed_users,
    auth_headers,
    monkeypatch,
    receipt_state,
):
    state = accepted_assessment
    run = state["run"]
    run.status = "running"
    db_session.commit()
    reservation = _reserve(db_session, run, item_id=run.item_id)
    receipt = lock_ai_provider_attempt_for_io(
        db_session,
        reservation=reservation,
        task_run_id=run.id,
        feature_type="team_assessment",
        item_id=run.item_id,
        daily_brief_id=None,
        report_id=None,
        request_fingerprint="a" * 64,
    )
    assert receipt.resource_type == "team_item_assessment"
    assert receipt.resource_id == state["row"].id
    if receipt_state == "ambiguous":
        settle_ai_provider_attempt(
            db_session,
            receipt_id=receipt.id,
            request_fingerprint=receipt.request_fingerprint,
            state="ambiguous",
            io_outcome="ambiguous",
            retryable=False,
            reservation_generation=receipt.reservation_generation,
        )
    run.status, run.finished_at = "error", datetime.now(timezone.utc)
    db_session.commit()

    another_team, _, _ = _team(client, db_session, seed_users, auth_headers)
    response = client.post(
        f"/items/{run.item_id}/team-assessment",
        json={"team_id": another_team["id"], "expected_version": 0},
        headers=auth_headers["analyst"],
    )
    assert response.status_code == 202, response.text
    another = db_session.get(
        TeamItemAssessment, uuid.UUID(response.json()["assessment"]["id"])
    )
    calls = []

    def provider(*_args, **_kwargs):
        calls.append(True)
        return _completion()

    monkeypatch.setattr("app.services.ai_integration._call_ai_json", provider)
    assert invoke_worker(db_session, monkeypatch, another.task_run_id) == {
        "status": "ready"
    }
    assert len(calls) == 1
    assert (
        db_session.scalar(
            select(AIProviderAttemptReceipt.resource_id).where(
                AIProviderAttemptReceipt.task_run_id_snapshot == another.task_run_id,
            )
        )
        == another.id
    )

    response = client.post(
        f"/items/{run.item_id}/team-assessment",
        json={
            "team_id": str(state["team_id"]),
            "expected_version": state["row"].version,
        },
        headers=auth_headers["analyst"],
    )
    assert response.status_code == 202, response.text
    db_session.refresh(state["row"])
    assert (
        invoke_worker(db_session, monkeypatch, state["row"].task_run_id)["status"]
        == "error"
    )
    assert len(calls) == 1, (
        "An unresolved receipt still forbids blind replay within one assessment"
    )
    retry = db_session.get(AITaskRun, state["row"].task_run_id)
    assert "reconciliation" in retry.error
    assert (
        db_session.scalar(
            select(AIProviderAttemptReceipt.id).where(
                AIProviderAttemptReceipt.task_run_id_snapshot == retry.id,
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "invalid_binding",
    ["missing_assessment", "invalid_assessment", "missing_item", "wrong_item"],
)
def test_receipts_require_valid_assessment_identity_and_matching_article(
    db_session,
    accepted_assessment,
    invalid_binding,
):
    run = accepted_assessment["run"]
    run.status = "running"
    metadata = dict(run.metadata_json)
    if invalid_binding == "missing_assessment":
        metadata.pop("assessment_id")
    elif invalid_binding == "invalid_assessment":
        metadata["assessment_id"] = "not-a-uuid"
    run.metadata_json = metadata
    db_session.commit()
    item_id = run.item_id
    if invalid_binding == "missing_item":
        item_id = None
    elif invalid_binding == "wrong_item":
        item_id = uuid.uuid4()
    with pytest.raises(AIProviderTaskBindingError) as rejected:
        _reserve(db_session, run, item_id=item_id)
    assert rejected.value.retryable is False
    assert db_session.scalar(select(AIProviderAttemptReceipt.id)) is None


def test_receipt_audit_and_two_person_approval_use_accepting_task_lineage(
    db_session,
    accepted_assessment,
    seed_users,
):
    run = accepted_assessment["run"]
    run.status = "running"
    db_session.commit()
    reservation = _reserve(db_session, run, item_id=run.item_id)
    receipt = db_session.get(AIProviderAttemptReceipt, reservation.receipt_id)
    envelope = get_data_access_envelope(
        db_session, resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN, resource_id=run.id
    )
    original_labels = envelope.label_ids
    # Changing the primary source cannot rewrite the accepting classification.
    db_session.get(
        Feed, accepted_assessment["item"].feed_id
    ).handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
    db_session.commit()
    labels = resolve_audit_data_access_labels(
        db_session,
        resource_type="ai_provider_attempt_receipt",
        resource_id=str(receipt.id),
    )
    assert labels == original_labels
    assert UNRESTRICTED_HANDLING_LABEL_ID not in labels
    snapshot = resolve_registered_action_target_data_access(
        db_session,
        definition=get_registered_action("ai.provider_attempt.confirm_not_sent"),
        target_resource=receipt,
        data_access=_context(
            db_session, mode="disabled", principal_id=seed_users["admin"].id
        ),
    )
    assert snapshot.data_access_scope == "governed"
    assert snapshot.data_access_source_id == run.id
    assert snapshot.copy_source_lineage is True
    assert snapshot.decision.label_ids == original_labels

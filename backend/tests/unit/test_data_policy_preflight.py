from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from sqlalchemy import update

from app.core.data_policy_route_attestation import (
    installed_route_governance_attestation,
)
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.data_policy import (
    DataAccessEnvelopeLabel,
    DataPolicyState,
    HandlingLabel,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.schemas.data_policy import HandlingLabelCreateRequest
from app.services import data_policy_preflight as preflight_service
from app.services.authorization import authorization_context_for_user
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_REPORT,
    DataAccessSourceInput,
    put_data_access_envelope_sources,
)
from app.services.data_access_policy import (
    DataPolicyActivationBlocked,
    create_handling_label,
    data_access_context_for_authorization,
    data_policy_overview,
    update_data_policy_mode,
)


def test_request_authorization_uses_only_cheap_runtime_invariants(
    db_session,
    seed_users,
    monkeypatch,
):
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    state.mode = "audit"
    db_session.flush()

    def reject_full_scan(_db):
        raise AssertionError("retained-data scan reached the request dependency")

    monkeypatch.setattr(
        preflight_service,
        "_retained_data_blockers",
        reject_full_scan,
    )
    authorization = authorization_context_for_user(
        db_session,
        seed_users["analyst"],
    )

    context = data_access_context_for_authorization(db_session, authorization)

    assert context.mode == "audit"
    with pytest.raises(AssertionError, match="retained-data scan"):
        data_policy_overview(db_session)


def test_full_preflight_blocks_missing_and_corrupt_route_attestation(
    db_session,
    monkeypatch,
):
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    installed = installed_route_governance_attestation()
    assert installed is not None

    monkeypatch.setattr(
        preflight_service,
        "installed_route_governance_attestation",
        lambda: None,
    )
    missing = preflight_service.full_data_policy_preflight(
        db_session,
        state=state,
    )
    assert missing.full is True
    assert missing.route_manifest.installed is False
    assert missing.ready_for_enforcement is False
    assert missing.blocker_counts == {"route_attestation_missing": 1}

    monkeypatch.setattr(
        preflight_service,
        "installed_route_governance_attestation",
        lambda: replace(installed, manifest_sha256="0" * 64),
    )
    corrupt = preflight_service.full_data_policy_preflight(
        db_session,
        state=state,
    )
    assert corrupt.route_manifest.installed is True
    assert corrupt.route_manifest.valid is False
    assert corrupt.route_manifest.digest == "0" * 64
    assert corrupt.blocker_counts == {"route_attestation_invalid": 1}


def test_full_preflight_detects_normalized_envelope_aggregate_corruption(
    db_session,
):
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    snapshot = put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=uuid.uuid4(),
        sources=[
            DataAccessSourceInput(
                source_type="item",
                source_id=str(uuid.uuid4()),
                source_version="v1",
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                captured_policy_revision=state.revision,
            )
        ],
    )
    db_session.execute(
        update(DataAccessEnvelopeLabel)
        .where(
            DataAccessEnvelopeLabel.envelope_id == snapshot.envelope_id,
            DataAccessEnvelopeLabel.label_id == UNRESTRICTED_HANDLING_LABEL_ID,
        )
        .values(source_count=2)
    )
    db_session.flush()

    preflight = preflight_service.full_data_policy_preflight(
        db_session,
        state=state,
    )

    assert preflight.ready_for_audit is False
    assert preflight.ready_for_enforcement is False
    assert preflight.blocker_counts["envelope_lineage_parity_invalid"] == 1


def test_activation_reruns_full_preflight_under_locked_policy_state(
    db_session,
    seed_users,
):
    initial = data_policy_overview(db_session)
    assert initial.preflight.ready_for_audit is True
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    snapshot = put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=uuid.uuid4(),
        sources=[
            DataAccessSourceInput(
                source_type="item",
                source_id=str(uuid.uuid4()),
                source_version="v1",
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                captured_policy_revision=state.revision,
            )
        ],
    )
    db_session.execute(
        update(DataAccessEnvelopeLabel)
        .where(
            DataAccessEnvelopeLabel.envelope_id == snapshot.envelope_id,
            DataAccessEnvelopeLabel.label_id == UNRESTRICTED_HANDLING_LABEL_ID,
        )
        .values(source_count=2)
    )
    db_session.flush()

    with pytest.raises(DataPolicyActivationBlocked) as raised:
        update_data_policy_mode(
            db_session,
            mode="audit",
            expected_revision=state.revision,
            actor_user_id=seed_users["admin"].id,
        )

    assert state.mode == "disabled"
    assert raised.value.context is not None
    assert any(
        blocker["code"] == "envelope_lineage_parity_invalid"
        for blocker in raised.value.context["blockers"]
    )


def test_full_preflight_rejects_malformed_system_ai_telemetry(
    db_session,
):
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    system_run = AITaskRun(
        task_type="connection_test",
        trigger_source="manual",
        status="succeeded",
        data_access_scope="system",
        data_access_lineage_complete=True,
    )
    db_session.add(system_run)
    db_session.flush()
    put_data_access_envelope_sources(
        db_session,
        resource_type="ai_task_run",
        resource_id=system_run.id,
        sources=[
            DataAccessSourceInput(
                source_type="system_test_corruption",
                source_id=str(system_run.id),
                source_version="v1",
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
                captured_policy_revision=state.revision,
            )
        ],
    )
    system_usage = AIUsageEvent(
        feature_type="connection_test",
        success=True,
        task_run_id_snapshot=uuid.uuid4(),
        data_access_scope="system",
    )
    db_session.add(system_usage)
    db_session.flush()

    preflight = preflight_service.full_data_policy_preflight(
        db_session,
        state=state,
    )

    assert preflight.blocker_counts["ai_task_run_scope_integrity_invalid"] == 1
    assert preflight.blocker_counts["ai_usage_event_scope_integrity_invalid"] == 1


def test_full_preflight_rejects_inactive_normalized_label_references(
    db_session,
    seed_users,
):
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    created = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=state.revision,
            key="preflight-inactive-lineage",
            name="Preflight inactive lineage",
        ),
        actor_user_id=seed_users["admin"].id,
    )
    snapshot = put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=uuid.uuid4(),
        sources=[
            DataAccessSourceInput(
                source_type="item",
                source_id=str(uuid.uuid4()),
                source_version="v1",
                handling_label_id=created.label.id,
                captured_policy_revision=created.policy_revision,
            )
        ],
    )
    assert snapshot.label_ids == {created.label.id}
    label = db_session.get(HandlingLabel, created.label.id)
    assert label is not None
    label.is_active = False
    db_session.flush()

    preflight = preflight_service.full_data_policy_preflight(
        db_session,
        state=state,
    )

    assert preflight.blocker_counts["inactive_normalized_label_references"] == 2

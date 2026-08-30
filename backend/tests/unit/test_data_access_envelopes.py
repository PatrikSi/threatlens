from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.schemas.data_policy import HandlingLabelCreateRequest
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_REPORT,
    DataAccessEnvelopeConflict,
    DataPolicyEgressDenied,
    data_access_envelope_predicate,
    evaluate_data_access_envelope,
    get_data_access_envelope,
    merge_data_access_envelope,
    put_data_access_envelope,
    require_data_access_for_egress,
    unrestricted_label_counts,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyUnavailable,
    create_handling_label,
)


def _context(
    *, mode: str = "enforced", allowed: frozenset[uuid.UUID] | None = None
) -> DataAccessContext:
    return DataAccessContext(
        mode=mode,  # type: ignore[arg-type]
        policy_revision=1,
        coverage_version=1,
        principal_type="user",
        principal_id=uuid.uuid4(),
        principal_eligible=True,
        allowed_label_ids=(
            allowed
            if allowed is not None
            else frozenset({UNRESTRICTED_HANDLING_LABEL_ID})
        ),
    )


def test_envelope_is_idempotent_and_conflicting_rewrites_are_rejected(db_session):
    resource_id = uuid.uuid4()

    created = put_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        label_counts=unrestricted_label_counts(2),
        source_count=2,
    )
    replay = put_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        label_counts=unrestricted_label_counts(2),
        source_count=2,
    )

    assert replay == created
    assert get_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
    ) == created

    with pytest.raises(DataAccessEnvelopeConflict, match="different"):
        put_data_access_envelope(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=resource_id,
            label_counts={QUARANTINE_HANDLING_LABEL_ID: 1},
            source_count=1,
        )


def test_merge_preserves_all_source_labels_and_counts(db_session, seed_users):
    restricted = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=1,
            key="restricted-envelope",
            name="Restricted envelope",
        ),
        actor_user_id=seed_users["admin"].id,
    ).label
    resource_id = uuid.uuid4()
    put_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        label_counts=unrestricted_label_counts(),
        source_count=1,
    )

    merged = merge_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        label_counts={restricted.id: 2},
        source_count_increment=2,
    )

    assert merged.source_count == 3
    assert merged.label_counts == {
        UNRESTRICTED_HANDLING_LABEL_ID: 1,
        restricted.id: 2,
    }


def test_enforced_predicate_requires_an_envelope_and_every_label(
    db_session, seed_users
):
    restricted = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=1,
            key="predicate-restricted",
            name="Predicate restricted",
        ),
        actor_user_id=seed_users["admin"].id,
    ).label
    allowed_id = uuid.uuid4()
    denied_id = uuid.uuid4()
    missing_id = uuid.uuid4()
    put_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=allowed_id,
        label_counts=unrestricted_label_counts(),
        source_count=1,
    )
    put_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=denied_id,
        label_counts={UNRESTRICTED_HANDLING_LABEL_ID: 1, restricted.id: 1},
        source_count=2,
    )

    visible = set(
        db_session.scalars(
            select(DataAccessEnvelope.resource_id).where(
                DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                DataAccessEnvelope.resource_id.in_([allowed_id, denied_id, missing_id]),
                data_access_envelope_predicate(
                    DATA_ACCESS_RESOURCE_REPORT,
                    DataAccessEnvelope.resource_id,
                    _context(),
                ),
            )
        ).all()
    )

    assert visible == {allowed_id}


def test_audit_and_egress_distinguish_denial_from_missing_provenance(
    db_session, seed_users
):
    restricted = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=1,
            key="egress-restricted",
            name="Egress restricted",
        ),
        actor_user_id=seed_users["admin"].id,
    ).label
    resource_id = uuid.uuid4()
    put_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        label_counts={restricted.id: 1},
        source_count=1,
    )

    audit_decision = evaluate_data_access_envelope(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        context=_context(mode="audit"),
    )
    assert audit_decision.allowed is True
    assert audit_decision.would_deny is True

    with pytest.raises(DataPolicyEgressDenied):
        require_data_access_for_egress(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=resource_id,
            context=_context(),
        )
    with pytest.raises(DataPolicyUnavailable, match="provenance is missing"):
        require_data_access_for_egress(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=uuid.uuid4(),
            context=_context(mode="audit"),
        )


def test_empty_or_corrupt_envelopes_fail_closed(db_session):
    resource_id = uuid.uuid4()
    envelope = DataAccessEnvelope(
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        source_count=0,
        policy_revision=1,
    )
    db_session.add(envelope)
    db_session.flush()

    with pytest.raises(DataPolicyUnavailable, match="no handling labels"):
        get_data_access_envelope(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=resource_id,
        )

    assert db_session.scalar(
        select(DataAccessEnvelopeLabel).where(
            DataAccessEnvelopeLabel.envelope_id == envelope.id
        )
    ) is None

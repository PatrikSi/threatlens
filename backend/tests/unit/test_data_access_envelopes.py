from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
    DataPolicyState,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.schemas.data_policy import HandlingLabelCreateRequest
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_REPORT,
    DataAccessEnvelopeConflict,
    DataAccessSourceInput,
    DataPolicyEgressDenied,
    copy_data_access_envelope_lineage,
    data_access_envelope_predicate,
    evaluate_data_access_envelope,
    get_data_access_envelope,
    get_data_access_envelope_sources,
    merge_data_access_envelope_sources,
    put_data_access_envelope_sources,
    replace_data_access_envelope_sources,
    require_data_access_for_egress,
    taint_data_access_envelopes_for_feed,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyUnavailable,
    assign_feed_handling_label,
    create_handling_label,
)


def _context(
    *,
    mode: str = "enforced",
    allowed: frozenset[uuid.UUID] | None = None,
    coverage_version: int = 1,
) -> DataAccessContext:
    return DataAccessContext(
        mode=mode,  # type: ignore[arg-type]
        policy_revision=1,
        coverage_version=coverage_version,
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
    revision = _policy_revision(db_session)
    sources = [
        _source(revision=revision),
        _source(revision=revision),
    ]

    created = put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=sources,
    )
    replay = put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=sources,
    )

    assert replay == created
    assert (
        get_data_access_envelope(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=resource_id,
        )
        == created
    )

    with pytest.raises(DataAccessEnvelopeConflict, match="different"):
        put_data_access_envelope_sources(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=resource_id,
            sources=[sources[0]],
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
    revision = _policy_revision(db_session)
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[_source(revision=revision)],
    )

    merged = merge_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[
            _source(revision=revision, label_id=restricted.id),
            _source(revision=revision, label_id=restricted.id),
        ],
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
    revision = _policy_revision(db_session)
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=allowed_id,
        sources=[_source(revision=revision)],
    )
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=denied_id,
        sources=[
            _source(revision=revision),
            _source(revision=revision, label_id=restricted.id),
        ],
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
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[
            _source(
                revision=_policy_revision(db_session),
                label_id=restricted.id,
            )
        ],
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

    assert (
        db_session.scalar(
            select(DataAccessEnvelopeLabel).where(
                DataAccessEnvelopeLabel.envelope_id == envelope.id
            )
        )
        is None
    )


def _policy_revision(db_session) -> int:
    return db_session.get(DataPolicyState, 1).revision


def _feed(db_session, *, suffix: str) -> Feed:
    feed = Feed(
        name=f"Lineage feed {suffix}",
        url=f"https://lineage-{suffix}.example.test/rss.xml",
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
    )
    db_session.add(feed)
    db_session.flush()
    return feed


def _source(
    *,
    revision: int,
    source_id: str | None = None,
    source_version: str = "v1",
    feed_id: uuid.UUID | None = None,
    label_id: uuid.UUID = UNRESTRICTED_HANDLING_LABEL_ID,
    digest: str | None = None,
) -> DataAccessSourceInput:
    return DataAccessSourceInput(
        source_type="item",
        source_id=source_id or str(uuid.uuid4()),
        source_version=source_version,
        source_feed_id=feed_id,
        handling_label_id=label_id,
        captured_policy_revision=revision,
        source_digest=digest,
    )


def test_normalized_source_merge_is_idempotent_and_never_double_counts(db_session):
    feed = _feed(db_session, suffix="merge")
    resource_id = uuid.uuid4()
    source = _source(revision=_policy_revision(db_session), feed_id=feed.id)

    created = put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[source],
    )
    replay = merge_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[source, source],
    )
    second_replay = merge_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[source],
    )

    assert replay == created
    assert second_replay == created
    assert replay.source_count == 1
    assert replay.label_counts == {UNRESTRICTED_HANDLING_LABEL_ID: 1}
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DataAccessEnvelopeSource)
            .where(DataAccessEnvelopeSource.envelope_id == replay.envelope_id)
        )
        == 1
    )


def test_normalized_source_identity_rejects_conflicting_rewrites(db_session):
    feed = _feed(db_session, suffix="conflict")
    resource_id = uuid.uuid4()
    source_id = str(uuid.uuid4())
    revision = _policy_revision(db_session)
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[
            _source(
                revision=revision,
                source_id=source_id,
                feed_id=feed.id,
            )
        ],
    )

    with pytest.raises(DataAccessEnvelopeConflict, match="cannot be rewritten"):
        merge_data_access_envelope_sources(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=resource_id,
            sources=[
                _source(
                    revision=revision,
                    source_id=source_id,
                    feed_id=feed.id,
                    digest="a" * 64,
                )
            ],
        )


def test_leaf_source_cannot_forge_a_feed_handling_label(db_session):
    feed = _feed(db_session, suffix="forged-label")
    resource_id = uuid.uuid4()

    with pytest.raises(DataAccessEnvelopeConflict, match="current handling label"):
        put_data_access_envelope_sources(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=resource_id,
            sources=[
                _source(
                    revision=_policy_revision(db_session),
                    feed_id=feed.id,
                    label_id=QUARANTINE_HANDLING_LABEL_ID,
                )
            ],
        )
    assert (
        db_session.scalar(
            select(DataAccessEnvelope.id).where(
                DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                DataAccessEnvelope.resource_id == resource_id,
            )
        )
        is None
    )


def test_nested_copy_preserves_locked_historical_feed_label(db_session):
    feed = _feed(db_session, suffix="historical-copy")
    report_id = uuid.uuid4()
    event_id = uuid.uuid4()
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report_id,
        sources=[_source(revision=_policy_revision(db_session), feed_id=feed.id)],
    )
    feed.handling_label_id = QUARANTINE_HANDLING_LABEL_ID
    db_session.add(feed)
    db_session.flush()

    copied = copy_data_access_envelope_lineage(
        db_session,
        source_resource_type=DATA_ACCESS_RESOURCE_REPORT,
        source_resource_id=report_id,
        target_resource_type="integration_event",
        target_resource_id=event_id,
    )

    assert copied.label_ids == {UNRESTRICTED_HANDLING_LABEL_ID}


def test_coverage_one_predicate_uses_normalized_sources_not_stale_aggregates(
    db_session,
):
    allowed_feed = _feed(db_session, suffix="source-predicate-allowed")
    denied_feed = _feed(db_session, suffix="source-predicate-denied")
    denied_feed.handling_label_id = QUARANTINE_HANDLING_LABEL_ID
    allowed_id = uuid.uuid4()
    denied_id = uuid.uuid4()
    revision = _policy_revision(db_session)
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=allowed_id,
        sources=[_source(revision=revision, feed_id=allowed_feed.id)],
    )
    denied = put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=denied_id,
        sources=[
            _source(
                revision=revision,
                feed_id=denied_feed.id,
                label_id=QUARANTINE_HANDLING_LABEL_ID,
            )
        ],
    )
    db_session.execute(
        DataAccessEnvelopeLabel.__table__.update()
        .where(DataAccessEnvelopeLabel.envelope_id == denied.envelope_id)
        .values(label_id=UNRESTRICTED_HANDLING_LABEL_ID)
    )
    db_session.flush()

    visible = set(
        db_session.scalars(
            select(DataAccessEnvelope.resource_id).where(
                DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_REPORT,
                DataAccessEnvelope.resource_id.in_([allowed_id, denied_id]),
                data_access_envelope_predicate(
                    DATA_ACCESS_RESOURCE_REPORT,
                    DataAccessEnvelope.resource_id,
                    _context(),
                ),
            )
        ).all()
    )

    assert visible == {allowed_id}


def test_nested_copy_flattens_leaf_sources_and_records_immediate_parent(db_session):
    feed = _feed(db_session, suffix="nested")
    revision = _policy_revision(db_session)
    report_id = uuid.uuid4()
    event_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    leaf = _source(revision=revision, feed_id=feed.id, digest="a" * 64)
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report_id,
        sources=[leaf],
    )

    copy_data_access_envelope_lineage(
        db_session,
        source_resource_type=DATA_ACCESS_RESOURCE_REPORT,
        source_resource_id=report_id,
        target_resource_type="integration_event",
        target_resource_id=event_id,
    )
    delivery = copy_data_access_envelope_lineage(
        db_session,
        source_resource_type="integration_event",
        source_resource_id=event_id,
        target_resource_type="integration_delivery",
        target_resource_id=delivery_id,
    )

    event_source = get_data_access_envelope_sources(
        db_session,
        resource_type="integration_event",
        resource_id=event_id,
    )[0]
    delivery_source = get_data_access_envelope_sources(
        db_session,
        resource_type="integration_delivery",
        resource_id=delivery_id,
    )[0]
    assert event_source.source_type == leaf.source_type
    assert event_source.source_id == leaf.source_id
    assert event_source.source_version == leaf.source_version
    report_source = get_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report_id,
    )[0]
    assert event_source.source_parent_id == report_source.id
    assert delivery_source.source_id == leaf.source_id
    assert delivery_source.source_parent_id == event_source.id
    assert delivery_source.source_parent_id != report_source.id
    assert delivery.source_count == 1


def test_same_leaf_can_be_reached_through_distinct_parent_sources(db_session):
    feed = _feed(db_session, suffix="multiple-parents")
    revision = _policy_revision(db_session)
    leaf_id = str(uuid.uuid4())
    first_report_id = uuid.uuid4()
    second_report_id = uuid.uuid4()
    investigation_id = uuid.uuid4()
    for report_id in (first_report_id, second_report_id):
        put_data_access_envelope_sources(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=report_id,
            sources=[
                _source(
                    revision=revision,
                    source_id=leaf_id,
                    source_version="same-snapshot",
                    feed_id=feed.id,
                )
            ],
        )

    copy_data_access_envelope_lineage(
        db_session,
        source_resource_type=DATA_ACCESS_RESOURCE_REPORT,
        source_resource_id=first_report_id,
        target_resource_type="investigation",
        target_resource_id=investigation_id,
    )
    copied = copy_data_access_envelope_lineage(
        db_session,
        source_resource_type=DATA_ACCESS_RESOURCE_REPORT,
        source_resource_id=second_report_id,
        target_resource_type="investigation",
        target_resource_id=investigation_id,
        operation="merge",
    )

    sources = get_data_access_envelope_sources(
        db_session,
        resource_type="investigation",
        resource_id=investigation_id,
    )
    assert copied.source_count == 2
    assert len({source.source_parent_id for source in sources}) == 2


def test_parent_source_cannot_be_removed_while_a_descendant_references_it(db_session):
    feed = _feed(db_session, suffix="retained-parent")
    revision = _policy_revision(db_session)
    report_id = uuid.uuid4()
    event_id = uuid.uuid4()
    original = _source(revision=revision, feed_id=feed.id)
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report_id,
        sources=[original],
    )
    copy_data_access_envelope_lineage(
        db_session,
        source_resource_type=DATA_ACCESS_RESOURCE_REPORT,
        source_resource_id=report_id,
        target_resource_type="integration_event",
        target_resource_id=event_id,
    )

    with pytest.raises(DataAccessEnvelopeConflict, match="descendant"):
        replace_data_access_envelope_sources(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=report_id,
            sources=[_source(revision=revision, feed_id=feed.id)],
        )

    assert (
        get_data_access_envelope_sources(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=report_id,
        )[0].source_id
        == original.source_id
    )


def test_feed_taint_propagates_to_every_flattened_descendant_once(
    db_session, seed_users
):
    feed = _feed(db_session, suffix="taint")
    report_id = uuid.uuid4()
    event_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=report_id,
        sources=[_source(revision=_policy_revision(db_session), feed_id=feed.id)],
    )
    copy_data_access_envelope_lineage(
        db_session,
        source_resource_type=DATA_ACCESS_RESOURCE_REPORT,
        source_resource_id=report_id,
        target_resource_type="integration_event",
        target_resource_id=event_id,
    )
    copy_data_access_envelope_lineage(
        db_session,
        source_resource_type="integration_event",
        source_resource_id=event_id,
        target_resource_type="integration_delivery",
        target_resource_id=delivery_id,
    )
    restricted = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=_policy_revision(db_session),
            key="lineage-taint",
            name="Lineage taint",
        ),
        actor_user_id=seed_users["admin"].id,
    ).label
    assignment = assign_feed_handling_label(
        db_session,
        feed_id=feed.id,
        handling_label_id=restricted.id,
        expected_policy_revision=_policy_revision(db_session),
        actor_user_id=seed_users["admin"].id,
    )
    revision = assignment.policy_revision

    for resource_type, resource_id in (
        (DATA_ACCESS_RESOURCE_REPORT, report_id),
        ("integration_event", event_id),
        ("integration_delivery", delivery_id),
    ):
        assert (
            restricted.id
            in get_data_access_envelope(
                db_session,
                resource_type=resource_type,
                resource_id=resource_id,
            ).label_ids
        )
    assert (
        taint_data_access_envelopes_for_feed(
            db_session,
            feed_id=feed.id,
            handling_label_id=restricted.id,
            policy_revision=revision,
        )
        == 0
    )
    for resource_type, resource_id in (
        (DATA_ACCESS_RESOURCE_REPORT, report_id),
        ("integration_event", event_id),
        ("integration_delivery", delivery_id),
    ):
        envelope = get_data_access_envelope(
            db_session,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        assert envelope is not None
        assert envelope.source_count == 2
        assert envelope.label_ids == {
            UNRESTRICTED_HANDLING_LABEL_ID,
            restricted.id,
        }
        assert [
            source.source_type
            for source in get_data_access_envelope_sources(
                db_session,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        ] == ["feed_taint", "item"]


@pytest.mark.parametrize(
    "source",
    [
        DataAccessSourceInput(
            source_type="item!",
            source_id="source",
            source_version="v1",
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
            captured_policy_revision=1,
        ),
        DataAccessSourceInput(
            source_type="item",
            source_id="source",
            source_version="v1",
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
            captured_policy_revision=1,
            source_digest="not-a-sha256",
        ),
        DataAccessSourceInput(
            source_type="item",
            source_id="source\ncontrol",
            source_version="v1",
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
            captured_policy_revision=1,
        ),
    ],
)
def test_malformed_normalized_sources_are_rejected(db_session, source):
    with pytest.raises(DataAccessEnvelopeConflict):
        put_data_access_envelope_sources(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=uuid.uuid4(),
            sources=[source],
        )


def test_inactive_source_labels_are_rejected(db_session, seed_users):
    inactive = create_handling_label(
        db_session,
        payload=HandlingLabelCreateRequest(
            expected_policy_revision=_policy_revision(db_session),
            key="inactive-lineage",
            name="Inactive lineage",
        ),
        actor_user_id=seed_users["admin"].id,
    ).label
    inactive = db_session.get(HandlingLabel, inactive.id)
    assert inactive is not None
    inactive.is_active = False
    db_session.add(inactive)
    db_session.flush()

    with pytest.raises(DataPolicyUnavailable, match="missing or inactive"):
        put_data_access_envelope_sources(
            db_session,
            resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=uuid.uuid4(),
            sources=[
                _source(
                    revision=_policy_revision(db_session),
                    label_id=inactive.id,
                )
            ],
        )


def test_replace_sources_recomputes_exact_aggregates(db_session):
    resource_id = uuid.uuid4()
    revision = _policy_revision(db_session)
    first = _source(revision=revision, source_id="first")
    second = _source(
        revision=revision,
        source_id="second",
        label_id=QUARANTINE_HANDLING_LABEL_ID,
    )
    put_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[first, second],
    )

    replaced = replace_data_access_envelope_sources(
        db_session,
        resource_type=DATA_ACCESS_RESOURCE_REPORT,
        resource_id=resource_id,
        sources=[second],
    )

    assert replaced.source_count == 1
    assert replaced.label_counts == {QUARANTINE_HANDLING_LABEL_ID: 1}

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.access_review import (
    AccessReviewApplyReceipt,
    AccessReviewCampaign,
    AccessReviewDecision,
    AccessReviewItem,
)
from app.services.access_review_queries import (
    AccessReviewQueryInvalid,
    get_access_review_campaign,
    list_access_review_campaigns,
    list_access_review_items,
)
from app.services.access_reviews import AccessReviewNotFound


_BACKEND_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class _SeededReviews:
    history_campaign_id: uuid.UUID
    overdue_campaign_id: uuid.UUID
    closed_campaign_id: uuid.UUID
    future_campaign_id: uuid.UUID
    item_ids: tuple[uuid.UUID, ...]


def _alembic_config() -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return config


def _database_url_for_schema(database_url: str, schema_name: str) -> str:
    return (
        make_url(database_url)
        .update_query_dict({"options": f"-csearch_path={schema_name},public"})
        .render_as_string(hide_password=False)
    )


@pytest.fixture(scope="module")
def access_review_query_store(test_database_url):
    schema_name = f"queries_0068_{uuid.uuid4().hex}"
    schema_url = _database_url_for_schema(test_database_url, schema_name)
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    schema_engine = create_engine(schema_url)

    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        connection.execute(
            text(
                f'CREATE TABLE "{schema_name}".alembic_version '
                "(version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
            )
        )

    try:
        with pytest.MonkeyPatch.context() as migration_env:
            migration_env.setenv("DATABASE_URL", schema_url.replace("%", "%%"))
            get_settings.cache_clear()
            command.upgrade(_alembic_config(), "0068_access_reviews")
            seeded = _seed_access_reviews(schema_engine)
            yield schema_engine, seeded
    finally:
        get_settings.cache_clear()
        schema_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_campaign_projection_counts_status_pagination_and_overdue(
    access_review_query_store,
):
    engine, seeded = access_review_query_store
    with Session(engine) as db:
        history = get_access_review_campaign(db, seeded.history_campaign_id)
        assert history.status == "applying"
        assert history.is_overdue is False
        assert history.item_count == 5
        assert history.decided_item_count == 5
        assert history.revoke_item_count == 2
        assert history.apply_terminal_item_count == 2

        overdue = get_access_review_campaign(db, seeded.overdue_campaign_id)
        assert overdue.is_overdue is True
        assert overdue.item_count == 1
        assert overdue.decided_item_count == 0

        closed = get_access_review_campaign(db, seeded.closed_campaign_id)
        assert closed.status == "closed"
        assert closed.review_due_at < datetime.now(timezone.utc)
        assert closed.is_overdue is False
        assert closed.decided_item_count == 1

        first_page = list_access_review_campaigns(db, page=1, page_size=2)
        second_page = list_access_review_campaigns(db, page=2, page_size=2)
        assert first_page.total == second_page.total == 4
        assert [campaign.id for campaign in first_page.campaigns] == [
            seeded.history_campaign_id,
            seeded.overdue_campaign_id,
        ]
        assert [campaign.id for campaign in second_page.campaigns] == [
            seeded.closed_campaign_id,
            seeded.future_campaign_id,
        ]

        closed_page = list_access_review_campaigns(
            db,
            page=1,
            page_size=100,
            status="closed",
        )
        assert closed_page.total == 1
        assert [campaign.id for campaign in closed_page.campaigns] == [
            seeded.closed_campaign_id
        ]

        with pytest.raises(AccessReviewNotFound, match="campaign not found"):
            get_access_review_campaign(db, uuid.uuid4())
        with pytest.raises(AccessReviewQueryInvalid, match="between 1 and 100"):
            list_access_review_campaigns(db, page=1, page_size=101)


def test_item_projection_uses_latest_history_and_filters(
    access_review_query_store,
):
    engine, seeded = access_review_query_store
    item_1, item_2, item_3, item_4, item_5 = seeded.item_ids

    with Session(engine) as db:
        first_page = list_access_review_items(
            db,
            campaign_id=seeded.history_campaign_id,
            page=1,
            page_size=2,
        )
        last_page = list_access_review_items(
            db,
            campaign_id=seeded.history_campaign_id,
            page=3,
            page_size=2,
        )
        assert first_page.total == last_page.total == 5
        assert [item.id for item in first_page.items] == [item_1, item_2]
        assert [item.id for item in last_page.items] == [item_5]
        assert first_page.items[0].latest_decision is not None
        assert first_page.items[0].latest_decision.sequence == 2
        assert first_page.items[0].latest_decision.decision == "revoke"
        assert first_page.items[0].latest_apply_receipt is not None
        assert first_page.items[0].latest_apply_receipt.attempt == 2
        assert first_page.items[0].latest_apply_receipt.outcome == "revoked"

        assert _filtered_item_ids(db, seeded, decision="revoke") == [item_1, item_4]
        assert _filtered_item_ids(db, seeded, decision="retain") == [
            item_2,
            item_3,
            item_5,
        ]
        assert _filtered_item_ids(db, seeded, decision="undecided") == []
        overdue_undecided = list_access_review_items(
            db,
            campaign_id=seeded.overdue_campaign_id,
            page=1,
            page_size=100,
            decision="undecided",
        )
        assert len(overdue_undecided.items) == 1
        assert _filtered_item_ids(db, seeded, apply_outcome="failed") == [item_4]
        assert _filtered_item_ids(db, seeded, apply_outcome="revoked") == [item_1]
        assert _filtered_item_ids(db, seeded, apply_outcome="not_applied") == [
            item_3,
            item_5,
        ]
        assert _filtered_item_ids(db, seeded, item_type="service_account_role") == [
            item_3
        ]
        assert _filtered_item_ids(db, seeded, principal_type="oidc_provider") == [
            item_4
        ]

        with pytest.raises(AccessReviewQueryInvalid, match="apply_outcome"):
            list_access_review_items(
                db,
                campaign_id=seeded.history_campaign_id,
                page=1,
                page_size=25,
                apply_outcome="unknown",
            )
        with pytest.raises(AccessReviewNotFound, match="campaign not found"):
            list_access_review_items(
                db,
                campaign_id=uuid.uuid4(),
                page=1,
                page_size=25,
            )


def test_item_projection_query_count_is_constant(access_review_query_store):
    engine, seeded = access_review_query_store
    selects: list[str] = []

    def capture_selects(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", capture_selects)
    try:
        with Session(engine) as db:
            response = list_access_review_items(
                db,
                campaign_id=seeded.history_campaign_id,
                page=1,
                page_size=100,
            )
            assert len(response.items) == 5
    finally:
        event.remove(engine, "before_cursor_execute", capture_selects)

    assert len(selects) == 3


def test_campaign_projection_bounds_history_to_the_requested_page(
    access_review_query_store,
):
    engine, _seeded = access_review_query_store
    selects: list[str] = []

    def capture_selects(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine, "before_cursor_execute", capture_selects)
    try:
        with Session(engine) as db:
            response = list_access_review_campaigns(db, page=1, page_size=2)
            assert len(response.campaigns) == 2
    finally:
        event.remove(engine, "before_cursor_execute", capture_selects)

    assert len(selects) == 4
    history_projection = selects[2]
    assert "access_review_decisions.campaign_id IN" in history_projection
    assert "access_review_apply_receipts.campaign_id IN" in history_projection


def _filtered_item_ids(
    db: Session,
    seeded: _SeededReviews,
    **filters: str,
) -> list[uuid.UUID]:
    response = list_access_review_items(
        db,
        campaign_id=seeded.history_campaign_id,
        page=1,
        page_size=100,
        **filters,
    )
    return [item.id for item in response.items]


def _seed_access_reviews(engine: Engine) -> _SeededReviews:
    with Session(engine) as db:
        now = db.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        history_campaign = _campaign(
            item_count=5,
            name="Access review with history",
            snapshot_at=now - timedelta(hours=1),
            review_due_at=now + timedelta(days=5),
            created_at=now - timedelta(hours=1),
        )
        overdue_campaign = _campaign(
            item_count=1,
            name="Overdue access review",
            snapshot_at=now - timedelta(days=3),
            review_due_at=now - timedelta(days=2),
            created_at=now - timedelta(hours=2),
        )
        closed_campaign = _campaign(
            item_count=1,
            name="Closed access review",
            snapshot_at=now - timedelta(days=5),
            review_due_at=now - timedelta(days=4),
            created_at=now - timedelta(hours=3),
        )
        future_campaign = _campaign(
            item_count=1,
            name="Future access review",
            snapshot_at=now - timedelta(hours=1),
            review_due_at=now + timedelta(days=5),
            created_at=now - timedelta(hours=4),
        )
        db.add_all(
            [
                history_campaign,
                overdue_campaign,
                closed_campaign,
                future_campaign,
            ]
        )
        db.flush()

        item_specs = (
            ("direct_user_role", "local", "user", "role"),
            ("group_membership", "local", "user", "group"),
            ("service_account_role", "local", "service_account", "role"),
            ("oidc_role_mapping", "oidc", "oidc_provider", "role"),
            ("live_elevation", "temporary", "user", "role"),
        )
        review_items = tuple(
            _item(
                campaign_id=history_campaign.id,
                ordinal=ordinal,
                item_type=item_type,
                assignment_source=source,
                principal_type=principal_type,
                target_type=target_type,
                now=now,
            )
            for ordinal, (item_type, source, principal_type, target_type) in enumerate(
                item_specs, start=1
            )
        )
        overdue_item = _item(
            campaign_id=overdue_campaign.id,
            ordinal=1,
            item_type="direct_user_role",
            assignment_source="local",
            principal_type="user",
            target_type="role",
            now=now,
        )
        closed_item = _item(
            campaign_id=closed_campaign.id,
            ordinal=1,
            item_type="direct_user_role",
            assignment_source="local",
            principal_type="user",
            target_type="role",
            now=now,
        )
        future_item = _item(
            campaign_id=future_campaign.id,
            ordinal=1,
            item_type="direct_user_role",
            assignment_source="local",
            principal_type="user",
            target_type="role",
            now=now,
        )
        db.add_all([*review_items, overdue_item, closed_item, future_item])
        db.flush()

        decisions = {
            "item_1_old": _decision(review_items[0], 1, "retain", now),
            "item_1_latest": _decision(review_items[0], 2, "revoke", now),
            "item_2_latest": _decision(review_items[1], 1, "retain", now),
            "item_3_latest": _decision(review_items[2], 1, "retain", now),
            "item_4_latest": _decision(review_items[3], 1, "revoke", now),
            "item_5_old": _decision(review_items[4], 1, "revoke", now),
            "item_5_latest": _decision(review_items[4], 2, "retain", now),
            "closed_item_latest": _decision(closed_item, 1, "retain", now),
        }
        db.add_all(decisions.values())
        db.flush()

        apply_run_id = uuid.uuid4()
        history_campaign.status = "closed"
        history_campaign.revision = 2
        history_campaign.closed_by_email_snapshot = "reviewer@example.test"
        history_campaign.closed_at = now
        history_campaign.close_reason = "The review was completed"
        closed_campaign.status = "closed"
        closed_campaign.revision = 2
        closed_campaign.closed_by_email_snapshot = "reviewer@example.test"
        closed_campaign.closed_at = now
        closed_campaign.close_reason = "The review was completed"
        db.flush()

        history_campaign.status = "applying"
        history_campaign.revision = 3
        history_campaign.apply_started_by_email_snapshot = "applier@example.test"
        history_campaign.apply_started_at = now + timedelta(seconds=1)
        history_campaign.apply_run_id = apply_run_id
        db.flush()

        db.add_all(
            [
                _receipt(
                    review_items[0],
                    decisions["item_1_latest"],
                    attempt=1,
                    outcome="drifted",
                    now=now,
                    apply_run_id=apply_run_id,
                ),
                _receipt(
                    review_items[0],
                    decisions["item_1_latest"],
                    attempt=2,
                    outcome="revoked",
                    now=now,
                    apply_run_id=apply_run_id,
                ),
                _receipt(
                    review_items[1],
                    decisions["item_2_latest"],
                    attempt=1,
                    outcome="retained",
                    now=now,
                    apply_run_id=apply_run_id,
                ),
                _receipt(
                    review_items[3],
                    decisions["item_4_latest"],
                    attempt=1,
                    outcome="drifted",
                    now=now,
                    apply_run_id=apply_run_id,
                ),
                _receipt(
                    review_items[3],
                    decisions["item_4_latest"],
                    attempt=2,
                    outcome="failed",
                    now=now,
                    apply_run_id=apply_run_id,
                ),
            ]
        )
        db.commit()
        return _SeededReviews(
            history_campaign_id=history_campaign.id,
            overdue_campaign_id=overdue_campaign.id,
            closed_campaign_id=closed_campaign.id,
            future_campaign_id=future_campaign.id,
            item_ids=tuple(item.id for item in review_items),
        )


def _campaign(
    *,
    item_count: int,
    name: str,
    snapshot_at: datetime,
    review_due_at: datetime,
    created_at: datetime,
) -> AccessReviewCampaign:
    return AccessReviewCampaign(
        id=uuid.uuid4(),
        name=name,
        description="Projection test campaign",
        scope_snapshot={"schema_version": 1},
        scope_digest=uuid.uuid4().hex * 2,
        snapshot_at=snapshot_at,
        review_due_at=review_due_at,
        item_count=item_count,
        created_by_user_id=None,
        created_by_email_snapshot="creator@example.test",
        status="open",
        revision=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _item(
    *,
    campaign_id: uuid.UUID,
    ordinal: int,
    item_type: str,
    assignment_source: str,
    principal_type: str,
    target_type: str,
    now: datetime,
) -> AccessReviewItem:
    return AccessReviewItem(
        id=uuid.uuid4(),
        campaign_id=campaign_id,
        ordinal=ordinal,
        item_type=item_type,
        assignment_id=uuid.uuid4(),
        assignment_source=assignment_source,
        assignment_revision_snapshot=1,
        assignment_fingerprint=uuid.uuid4().hex * 2,
        principal_type=principal_type,
        principal_id_snapshot=uuid.uuid4(),
        principal_label_snapshot=f"Principal {ordinal}",
        target_type=target_type,
        target_id_snapshot=uuid.uuid4(),
        target_key_snapshot=f"target-{ordinal}",
        target_label_snapshot=f"Target {ordinal}",
        target_revision_snapshot=1,
        permissions_snapshot=["read:audit"],
        provenance_snapshot={"schema_version": 1},
        assignment_created_at_snapshot=now - timedelta(days=30),
        access_expires_at_snapshot=(
            now + timedelta(days=1) if item_type == "live_elevation" else None
        ),
        created_at=now,
    )


def _decision(
    item: AccessReviewItem,
    sequence: int,
    value: str,
    now: datetime,
) -> AccessReviewDecision:
    return AccessReviewDecision(
        id=uuid.uuid4(),
        campaign_id=item.campaign_id,
        item_id=item.id,
        item_fingerprint=item.assignment_fingerprint,
        sequence=sequence,
        decision=value,
        decided_by_user_id=None,
        decided_by_email_snapshot="reviewer@example.test",
        reason="Reviewed access assignment",
        decided_at=now + timedelta(seconds=sequence),
    )


def _receipt(
    item: AccessReviewItem,
    decision: AccessReviewDecision,
    *,
    attempt: int,
    outcome: str,
    now: datetime,
    apply_run_id: uuid.UUID,
) -> AccessReviewApplyReceipt:
    return AccessReviewApplyReceipt(
        id=uuid.uuid4(),
        campaign_id=item.campaign_id,
        item_id=item.id,
        item_fingerprint=item.assignment_fingerprint,
        decision_id=decision.id,
        apply_run_id=apply_run_id,
        attempt=attempt,
        outcome=outcome,
        expected_assignment_revision=1,
        observed_assignment_revision=1,
        expected_target_revision=1,
        observed_target_revision=1,
        observed_fingerprint=item.assignment_fingerprint,
        mutation_performed=outcome == "revoked",
        detail_code=f"apply_{outcome}",
        detail=f"Apply attempt ended as {outcome}",
        result_snapshot={"schema_version": 1},
        applied_by_user_id=None,
        applied_by_email_snapshot="applier@example.test",
        created_at=now + timedelta(minutes=attempt),
    )

#!/usr/bin/env python3
"""Populated enterprise migration contract for the disposable CI database only."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import os
import sys
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from upgrade_compatibility_fixture import FIXTURE_USER_ID, FIXTURE_VIEW_ID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASELINE = "0100_ai_evidence_provenance"
HEAD = "0105_workspace_enforcement"
NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def identity(number: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0100-4000-8000-{number:012d}")


def table(connection: Connection, name: str) -> Table:
    return Table(name, MetaData(), autoload_with=connection)


def seed(engine: Engine) -> None:
    with engine.begin() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == BASELINE
        )
        connection.execute(
            table(connection, "export_jobs")
            .insert()
            .values(
                id=identity(1),
                principal_type="user",
                principal_id=FIXTURE_USER_ID,
                idempotency_key=identity(2),
                request_hash="e" * 64,
                request_encrypted={"fixture": "opaque accepted request"},
                authorization_encrypted={"fixture": "opaque captured credential"},
                format="csv",
                status="queued",
                attempts=0,
                reserved_bytes=4096,
                completed_items=0,
                expires_at=NOW + timedelta(days=7),
                next_attempt_at=NOW,
            )
        )
        connection.execute(
            table(connection, "report_templates")
            .insert()
            .values(
                id=identity(3),
                owner_user_id=FIXTURE_USER_ID,
                name="Migration report template",
                focus_topics_json=[],
                excluded_topics_json=[],
                sections_json=[
                    {"key": "executive_summary", "title": "Executive Summary"}
                ],
                default_filters_json={},
            )
        )
        connection.execute(
            table(connection, "report_schedules")
            .insert()
            .values(
                id=identity(4),
                template_id=identity(3),
                owner_user_id=FIXTURE_USER_ID,
                name="Existing automatic report schedule",
                filters_json={},
                delivery_enabled=True,
                next_run_at=NOW,
            )
        )
        reports = table(connection, "reports")
        for number, status in ((5, "ready"), (6, "queued")):
            connection.execute(
                reports.insert().values(
                    id=identity(number),
                    owner_user_id=FIXTURE_USER_ID,
                    template_id=identity(3),
                    title=f"Migration {status} report",
                    status=status,
                    generation_stage=status,
                    period_start=NOW - timedelta(days=7),
                    period_end=NOW,
                    generated_at=NOW if status == "ready" else None,
                    filters_json={},
                    prompt_config_json={},
                    generation_context_json={},
                    sections_config_json=[
                        {"key": "executive_summary", "title": "Executive Summary"}
                    ],
                    metrics_json={},
                    coverage_json={"evidence_contract_version": 1},
                    summary_text="Retained migration evidence [S1].",
                    source_count=1,
                    included_source_count=1,
                    citation_count=1,
                    delivery_requested=True,
                )
            )
            connection.execute(
                table(connection, "report_sections")
                .insert()
                .values(
                    id=identity(10 + number),
                    report_id=identity(number),
                    section_key="executive_summary",
                    title="Executive Summary",
                    position=1,
                    status="ready",
                    body_markdown="Retained migration evidence [S1].",
                    key_points_json=[],
                    citations_json=["S1"],
                )
            )
            connection.execute(
                table(connection, "report_source_items")
                .insert()
                .values(
                    id=identity(20 + number),
                    report_id=identity(number),
                    citation_key="S1",
                    included=True,
                    title_snapshot="Historical source",
                    feed_name_snapshot="Historical feed",
                    url_snapshot="https://migration.example.invalid/source",
                    first_seen_at_snapshot=NOW,
                    tags_snapshot_json=[],
                    iocs_snapshot_json=[],
                    evidence_text="Retained migration evidence.",
                )
            )
        connection.execute(
            table(connection, "integration_events")
            .insert()
            .values(
                id=identity(7),
                event_type="report_ready",
                schema_version=2,
                source_type="report",
                source_id=str(identity(5)),
                actor_user_id=FIXTURE_USER_ID,
                idempotency_key=f"report:{identity(5)}:ready:v1",
                payload_json={
                    "schema_version": 2,
                    "report_id": str(identity(5)),
                    "owner_user_id": str(FIXTURE_USER_ID),
                    "daily_brief": {
                        "id": str(identity(5)),
                        "text": "Retained migration evidence [S1].",
                    },
                },
            )
        )
        connection.execute(
            table(connection, "alert_interests")
            .insert()
            .values(
                id=identity(8),
                user_id=FIXTURE_USER_ID,
                name="Personal migration rule",
                category="keyword",
                keywords=["migration"],
            )
        )
        connection.execute(
            table(connection, "investigations")
            .insert()
            .values(
                id=identity(9),
                title="Personal migration investigation",
                created_by_user_id=FIXTURE_USER_ID,
            )
        )


def verify(engine: Engine) -> None:
    from app.models.integration import IntegrationEvent
    from app.services.report_event_compatibility import report_ready_event_owner_id

    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        )
        export = connection.execute(
            text(
                "SELECT status, attempts, expires_at, published_at, published_canary_at FROM export_jobs WHERE id=:id"
            ),
            {"id": identity(1)},
        ).one()
        assert tuple(export) == ("queued", 0, NOW + timedelta(days=7), None, None)
        for number, status, publication in (
            (5, "ready", "published"),
            (6, "queued", "draft"),
        ):
            report = connection.execute(
                text(
                    "SELECT status, publication_status, review_required, editorial_contract_version, published_revision_hash, summary_text FROM reports WHERE id=:id"
                ),
                {"id": identity(number)},
            ).one()
            assert tuple(report) == (
                status,
                publication,
                False,
                0,
                None,
                "Retained migration evidence [S1].",
            )
        assert (
            connection.scalar(
                text("SELECT review_required FROM report_schedules WHERE id=:id"),
                {"id": identity(4)},
            )
            is False
        )
        view = connection.execute(
            text("SELECT user_id, team_id, revision FROM saved_views WHERE id=:id"),
            {"id": FIXTURE_VIEW_ID},
        ).one()
        assert tuple(view) == (FIXTURE_USER_ID, None, 1)
        assert (
            connection.scalar(
                text("SELECT team_id FROM investigations WHERE id=:id"),
                {"id": identity(9)},
            )
            is None
        )
        rule = connection.execute(
            text(
                "SELECT user_id, team_id, due_after_minutes, escalation_after_minutes FROM alert_interests WHERE id=:id"
            ),
            {"id": identity(8)},
        ).one()
        assert tuple(rule) == (FIXTURE_USER_ID, None, None, None)
        assert (
            connection.scalar(text("SELECT count(*) FROM workspace_role_policies")) == 3
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM workspace_role_policies WHERE landing_mode <> 'default' OR dashboard_mode <> 'default' OR dashboard_view_json IS NOT NULL"
                )
            )
            == 0
        )
    # The existing schema-v2 queued delivery remains valid; migration does not
    # manufacture a historical approval or force regeneration of retained content.
    with Session(engine) as db:
        event = db.get(IntegrationEvent, identity(7))
        assert report_ready_event_owner_id(db, event=event) == FIXTURE_USER_ID
        assert (
            event.payload_json["daily_brief"]["text"]
            == "Retained migration evidence [S1]."
        )


def _expect_guard(engine: Engine, config: Config, message: str) -> None:
    try:
        command.downgrade(config, "0101_export_dispatch_progress")
    except RuntimeError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError(f"Downgrade did not protect {message}")
    with engine.connect() as connection:
        # DDL/version changes made by later downgrade steps must roll back too.
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_name='workspace_role_policies' AND column_name='landing_mode'"
                )
            )
            == 1
        )


def verify_guards(engine: Engine) -> None:
    from app.models.iam import IAMGroup
    from app.models.saved_view import SavedView
    from app.models.team import Team

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "alembic")
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE reports SET editorial_version=4, review_required=true, published_revision_hash=:hash WHERE id=:id"
            ),
            {"id": identity(5), "hash": "a" * 64},
        )
    _expect_guard(engine, config, "editorial history")
    with engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT published_revision_hash FROM reports WHERE id=:id"),
                {"id": identity(5)},
            )
            == "a" * 64
        )
        connection.execute(
            text(
                "UPDATE reports SET editorial_version=1, review_required=false, published_revision_hash=NULL WHERE id=:id"
            ),
            {"id": identity(5)},
        )
    with Session(engine) as db:
        group = IAMGroup(
            id=identity(30),
            key="migration-fixture-group",
            name="Migration team members",
        )
        db.add(group)
        db.flush()
        db.add(
            Team(
                id=identity(31),
                key="migration-fixture-team",
                name="Migration team",
                membership_group_id=group.id,
            )
        )
        db.flush()
        db.add(
            SavedView(
                id=identity(32),
                team_id=identity(31),
                name="Protected team view",
                query_json={"windows": []},
            )
        )
        db.commit()
    _expect_guard(engine, config, "team-owned views and investigations")
    with engine.begin() as connection:
        assert connection.scalar(
            text("SELECT team_id FROM saved_views WHERE id=:id"), {"id": identity(32)}
        ) == identity(31)
        connection.execute(
            text("DELETE FROM saved_views WHERE id=:id"), {"id": identity(32)}
        )
        connection.execute(
            table(connection, "alert_interests")
            .insert()
            .values(
                id=identity(33),
                team_id=identity(31),
                user_id=None,
                name="Protected team rule",
                category="keyword",
                keywords=["migration"],
            )
        )
    _expect_guard(engine, config, "team alert")
    with engine.begin() as connection:
        assert connection.scalar(
            text("SELECT team_id FROM alert_interests WHERE id=:id"),
            {"id": identity(33)},
        ) == identity(31)
        connection.execute(
            text("DELETE FROM alert_interests WHERE id=:id"), {"id": identity(33)}
        )
        connection.execute(text("DELETE FROM teams WHERE id=:id"), {"id": identity(31)})
        connection.execute(
            text("DELETE FROM iam_groups WHERE id=:id"), {"id": identity(30)}
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "verify", "verify-guards"))
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        {"seed": seed, "verify": verify, "verify-guards": verify_guards}[args.action](
            engine
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

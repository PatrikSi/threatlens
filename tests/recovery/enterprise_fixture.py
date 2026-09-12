"""Seed and check synthetic enterprise records inside the disposable API image."""

from datetime import datetime, timedelta, timezone
import json
import sys
import uuid

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.alert_interest import AlertInterest
from app.models.audit_log import AuditLog
from app.models.iam import IAMGroup, IAMGroupMembership
from app.models.integration import IntegrationEvent
from app.models.investigation import Investigation
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.models.saved_view import SavedView
from app.models.team import Team
from app.models.user import User
from app.models.workspace import WorkspaceRolePolicy
from app.schemas.report_editorial import ReportEditorialTransition
from app.services.report_editorial import transition_report
from app.services.report_publication import require_report_publication


def identity(number: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0105-4000-8000-{number:012d}")


def seed() -> None:
    publication_pins = {}
    with SessionLocal() as db:
        owner = db.scalar(
            select(User).where(User.email == "recovery-e2e@invalid.example")
        )
        db.add(IAMGroup(id=identity(1), key="recovery-fixture", name="Recovery team"))
        db.flush()
        db.add_all(
            [
                IAMGroupMembership(
                    group_id=identity(1),
                    user_id=owner.id,
                    source="local",
                    source_key="",
                ),
                Team(
                    id=identity(2),
                    key="recovery-fixture",
                    name="Recovery team",
                    membership_group_id=identity(1),
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                SavedView(
                    id=identity(3),
                    team_id=identity(2),
                    name="Recovery shared view",
                    query_json={"filters": {"q": "recovery"}},
                ),
                Investigation(
                    id=identity(4),
                    team_id=identity(2),
                    created_by_user_id=owner.id,
                    title="Recovery investigation",
                    visibility="team",
                ),
                AlertInterest(
                    id=identity(5),
                    team_id=identity(2),
                    name="Recovery team rule",
                    category="keyword",
                    keywords=["recovery"],
                    due_after_minutes=60,
                    escalation_after_minutes=30,
                ),
            ]
        )
        policy = db.get(WorkspaceRolePolicy, "analyst")
        policy.landing_mode = "enforced"
        policy.landing_module_id = "primary.stats"
        now = datetime.now(timezone.utc)
        for number, delivery in ((10, False), (11, True)):
            report = Report(
                id=identity(number),
                owner_user_id=owner.id,
                title=f"Retained publication {number}",
                status="ready",
                generation_stage="ready",
                period_start=now - timedelta(days=7),
                period_end=now,
                generated_at=now,
                summary_text="Retained evidence [S1].",
                delivery_requested=delivery,
                source_count=1,
                included_source_count=1,
                citation_count=1,
            )
            db.add(report)
            db.flush()
            db.add_all(
                [
                    ReportSection(
                        report_id=report.id,
                        section_key="executive_summary",
                        title="Summary",
                        position=1,
                        status="ready",
                        body_markdown="Retained evidence [S1].",
                        key_points_json=[],
                        citations_json=["S1"],
                    ),
                    ReportSourceItem(
                        report_id=report.id,
                        citation_key="S1",
                        included=True,
                        rank=1,
                        title_snapshot="Historical source",
                        feed_name_snapshot="Historical feed",
                        url_snapshot="https://recovery.invalid/source",
                        first_seen_at_snapshot=now,
                        evidence_text="Retained evidence.",
                        tags_snapshot_json=[],
                        iocs_snapshot_json=[],
                        estimated_tokens=5,
                    ),
                ]
            )
            db.flush()
            for version, action in ((1, "submit"), (2, "approve"), (3, "publish")):
                transition_report(
                    db,
                    report=report,
                    actor_user_id=owner.id,
                    payload=ReportEditorialTransition(
                        expected_version=version,
                        action=action,
                        note="Reviewed recovery evidence.",
                    ),
                )
            publication_pins[str(number)] = report.published_revision_hash
        db.commit()
    print(json.dumps(publication_pins))


def verify() -> None:
    publication_pins = json.loads(sys.argv[2])
    with SessionLocal() as db:
        team = db.get(Team, identity(2))
        assert team.membership_group_id == identity(1)
        assert db.get(SavedView, identity(3)).team_id == team.id
        assert db.get(Investigation, identity(4)).team_id == team.id
        rule = db.get(AlertInterest, identity(5))
        assert (
            rule.team_id,
            rule.due_after_minutes,
            rule.escalation_after_minutes,
        ) == (team.id, 60, 30)
        assert db.get(WorkspaceRolePolicy, "analyst").landing_mode == "enforced"
        for number in (10, 11):
            report = db.get(Report, identity(number))
            assert report.published_revision_hash == publication_pins[str(number)]
            assert report.delivery_requested is (number == 11)
            assert report.publication_status == "published"
            assert report.editorial_version == 4
            assert (
                report.review_revision_hash
                == report.approved_revision_hash
                == report.published_revision_hash
            )
            assert report.approval_self_review is True
            assert report.approved_by_user_id == report.owner_user_id
            require_report_publication(db, report)
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.resource_id == str(report.id),
                        AuditLog.action.like("reports.editorial.%"),
                    )
                )
                == 3
            )
        event = db.scalar(
            select(IntegrationEvent).where(
                IntegrationEvent.source_id == str(identity(11)),
                IntegrationEvent.event_type == "report_ready",
            )
        )
        assert event.routing_state == "dead_letter"
        assert (
            event.payload_json["publication"]["revision_hash"]
            == db.get(Report, identity(11)).published_revision_hash
        )


if __name__ == "__main__":
    {"seed": seed, "verify": verify}[sys.argv[1]]()

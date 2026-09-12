"""Expiring group assertions are rechecked after real PostgreSQL row waits."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.models.alert_interest import AlertInterest
from app.models.alert_occurrence import AlertOccurrence
from app.models.iam import IAMGroup, IAMGroupMembership, IAMRole
from app.models.oidc import OIDCProvider
from app.models.team import Team
from app.models.user import User
from app.services.alert_occurrences import (
    bulk_update_alert_occurrence_lifecycle,
    get_alert_occurrence,
)
from app.services.alert_team_access import get_alert_rule_for_update
from app.services.authorization import authorization_context_for_user
from tests.integration.test_investigations_api import _disabled_data_access
from tests.integration.test_oidc_access_sync import _sync_fixture


@pytest.mark.parametrize("operation", ["occurrence", "bulk", "rule"])
def test_expired_oidc_membership_cannot_resume_a_waiting_team_write(
    database_engine, operation
):
    user_id, team_id, rule_id, occurrence_id = (uuid.uuid4() for _ in range(4))
    with Session(database_engine) as setup:
        user = User(
            id=user_id,
            email=f"triage-expiry-{user_id}@example.test",
            password_hash="x",
            role="analyst",
            is_active=True,
            is_approved=True,
        )
        setup.add(user)
        setup.flush()
        identity = _sync_fixture(setup, {"admin": user, "viewer": user})
        group_id = identity["group"].id
        provider_id = identity["provider"].id
        role_ids = [identity["mapped_role"].id, identity["local_assignment"].role_id]
        mapping = identity["group_mapping"]
        # Start with plenty of time for setup; establish the short test lifetime below.
        membership = IAMGroupMembership(
            group_id=group_id,
            user_id=user_id,
            source="oidc",
            source_key=mapping.source_key,
            oidc_group_mapping_id=mapping.id,
            oidc_assertion_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        setup.add_all(
            [
                membership,
                Team(
                    id=team_id,
                    key=f"triage-{team_id.hex}",
                    name="Federated triage",
                    membership_group_id=group_id,
                ),
            ]
        )
        setup.flush()
        setup.add(
            AlertInterest(
                id=rule_id,
                team_id=team_id,
                name="Federated rule",
                category="appliance",
                keywords=["test"],
            )
        )
        setup.add(
            AlertOccurrence(
                id=occurrence_id,
                team_id=team_id,
                rule_id_snapshot=rule_id,
                rule_revision=1,
                item_id_snapshot=uuid.uuid4(),
                item_content_hash="a" * 64,
                alert_name_snapshot="Federated rule",
                alert_category_snapshot="appliance",
                severity_snapshot="medium",
            )
        )
        setup.commit()
        membership.oidc_assertion_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=3
        )
        expires_at = membership.oidc_assertion_expires_at
        setup.commit()

    def resume_write():
        with Session(database_engine) as db:
            db.execute(text("SET application_name = 'threatlens-triage-expiry-test'"))
            actor = db.get(User, user_id)
            authorization = authorization_context_for_user(db, actor)
            data_access = _disabled_data_access(actor)
            try:
                if operation == "rule":
                    get_alert_rule_for_update(
                        db, user=actor, authorization=authorization, rule_id=rule_id
                    )
                elif operation == "bulk":
                    bulk_update_alert_occurrence_lifecycle(
                        db,
                        user=actor,
                        authorization=authorization,
                        data_access=data_access,
                        entries=[(occurrence_id, 1)],
                        target_state="acknowledged",
                        disposition=None,
                    )
                else:
                    get_alert_occurrence(
                        db,
                        user=actor,
                        authorization=authorization,
                        data_access=data_access,
                        occurrence_id=occurrence_id,
                        for_update=True,
                    )
                db.commit()
                return "accepted"
            except ApiHTTPException as exc:
                db.rollback()
                return exc.status_code

    try:
        with (
            Session(database_engine) as blocker,
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            resource = AlertInterest if operation == "rule" else AlertOccurrence
            resource_id = rule_id if operation == "rule" else occurrence_id
            blocker.scalar(
                select(resource).where(resource.id == resource_id).with_for_update()
            )
            future = executor.submit(resume_write)
            waiting = False
            deadline = time.monotonic() + 2
            with Session(database_engine) as monitor:
                while time.monotonic() < deadline:
                    waiting = bool(
                        monitor.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name = 'threatlens-triage-expiry-test' AND wait_event_type = 'Lock')"
                            )
                        )
                    )
                    monitor.rollback()
                    if waiting:
                        break
                    time.sleep(0.02)
            if not waiting:
                blocker.rollback()
            assert waiting, (
                "The writer must evaluate access before blocking on the resource row."
            )
            while datetime.now(timezone.utc) <= expires_at:
                time.sleep(0.05)
            blocker.commit()
            assert future.result(timeout=5) == 404
        with Session(database_engine) as verify:
            assert verify.get(AlertOccurrence, occurrence_id).version == 1
    finally:
        with Session(database_engine) as cleanup:
            cleanup.execute(
                delete(AlertOccurrence).where(AlertOccurrence.id == occurrence_id)
            )
            cleanup.execute(delete(AlertInterest).where(AlertInterest.id == rule_id))
            cleanup.execute(delete(Team).where(Team.id == team_id))
            cleanup.execute(
                delete(IAMGroupMembership).where(IAMGroupMembership.user_id == user_id)
            )
            cleanup.execute(delete(OIDCProvider).where(OIDCProvider.id == provider_id))
            cleanup.execute(delete(IAMGroup).where(IAMGroup.id == group_id))
            cleanup.execute(delete(IAMRole).where(IAMRole.id.in_(role_ids)))
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()

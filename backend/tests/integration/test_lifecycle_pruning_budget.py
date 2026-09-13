"""A locked LIMIT remains a batch limit under PostgreSQL nested-loop plans."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import func, select, text

from app.models.action_approval import ActionApprovalRequest
from app.models.audit_log import (
    AuditLog,
    AuditLogDataAccessFeed,
    AuditLogDataAccessLabel,
)
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.lifecycle_pruning import LifecyclePruningRecord
from app.models.user import User
from app.services.lifecycle_pruning import prune_oversized_parent
from app.services.lifecycle_pruning_contracts import PruningContext
from tests.integration.test_permission_history_pruning import _audit
from tests.unit.test_history_maintenance import _approval_record


@pytest.mark.parametrize("kind", ["approval_receipts", "audit_sources"])
def test_pruning_budget_is_stable_when_nested_loops_rescan_the_selection(
    db_session, kind
):
    old = datetime(2024, 1, 1, tzinfo=timezone.utc)
    cutoff = old + timedelta(days=1)
    # Insert in primary-key order. A rescan after deleting each first row would
    # otherwise keep selecting its replacement and delete all 25 children.
    child_ids = sorted(uuid.uuid4() for _ in range(25))
    if kind == "approval_receipts":
        user = User(
            email=f"pruning-budget-{uuid.uuid4()}@example.test",
            password_hash="unused",
            role="viewer",
        )
        db_session.add(user)
        db_session.flush()
        parent = _approval_record(created_at=old, status="denied", requester_id=user.id)
        db_session.add(parent)
        db_session.flush()
        db_session.add_all(
            GovernanceOperationReceipt(
                id=child_id,
                actor_user_id=user.id,
                operation="action_approval.read",
                key_hash=f"{index:064x}",
                request_fingerprint="e" * 64,
                resource_type="action_approval",
                resource_id=parent.id,
                response_json={},
                http_status=200,
                created_at=old,
            )
            for index, child_id in enumerate(child_ids)
        )
        model = ActionApprovalRequest
        child_model = GovernanceOperationReceipt
        child_parent = GovernanceOperationReceipt.resource_id
    else:
        parent = _audit(db_session, feeds=0)
        db_session.add_all(
            AuditLogDataAccessFeed(
                audit_log_id=parent.id, source_feed_id_snapshot=child_id
            )
            for child_id in child_ids
        )
        model = AuditLog
        child_model = AuditLogDataAccessFeed
        child_parent = AuditLogDataAccessFeed.audit_log_id
    parent_id = parent.id
    db_session.commit()

    # These are valid PostgreSQL execution settings. Without a stable CTE, the
    # DELETE may put its locked LIMIT on the inner side of a nested semi join.
    for option in (
        "enable_hashjoin",
        "enable_mergejoin",
        "enable_hashagg",
        "enable_material",
        "enable_sort",
    ):
        db_session.execute(
            text("SELECT set_config(:option, 'off', true)"), {"option": option}
        )
    result = prune_oversized_parent(
        db_session,
        model=model,
        parent_id=parent_id,
        context=PruningContext(cutoff, model.created_at < cutoff, parent_row_budget=7),
        limit=7,
    )
    assert result.children_pruned == 7
    db_session.commit()
    db_session.expunge_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(child_model)
            .where(child_parent == parent_id)
        )
        == 18
    )
    progress = db_session.get(LifecyclePruningRecord, (model.__table__.name, parent_id))
    assert progress.children_pruned == 7
    assert db_session.get(model, parent_id) is not None
    if kind == "audit_sources":
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(AuditLogDataAccessLabel)
                .where(AuditLogDataAccessLabel.audit_log_id == parent_id)
            )
            == 1
        )

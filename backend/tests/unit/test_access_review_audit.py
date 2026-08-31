from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.api.routes.access_reviews import _domain_revocation_audit_identity


@pytest.mark.parametrize(
    ("item_type", "expected_action", "expected_resource_type", "resource_field"),
    [
        ("direct_user_role", "iam.user_role.remove", "user", "principal"),
        ("group_membership", "iam.group_member.remove", "iam_group", "target"),
        (
            "service_account_role",
            "service_accounts.role.remove",
            "service_account",
            "principal",
        ),
        (
            "live_elevation",
            "elevations.grant.revoke",
            "temporary_elevation",
            "assignment",
        ),
    ],
)
def test_domain_revocation_audit_identity_matches_canonical_contract(
    item_type: str,
    expected_action: str,
    expected_resource_type: str,
    resource_field: str,
):
    assignment_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    target_id = uuid.uuid4()
    item = SimpleNamespace(
        item_type=item_type,
        assignment_id=assignment_id,
        principal_id_snapshot=principal_id,
        target_id_snapshot=target_id,
    )
    receipt = SimpleNamespace(result_snapshot={"resource_revision": 7})

    result = _domain_revocation_audit_identity(item, receipt)

    assert result is not None
    action, resource_type, resource_id, metadata = result
    assert action == expected_action
    assert resource_type == expected_resource_type
    assert (
        resource_id
        == {
            "assignment": assignment_id,
            "principal": principal_id,
            "target": target_id,
        }[resource_field]
    )
    if item_type == "direct_user_role":
        assert metadata == {
            "assignment_id": str(assignment_id),
            "role_id": str(target_id),
        }
    elif item_type == "group_membership":
        assert metadata == {
            "membership_id": str(assignment_id),
            "user_id": str(principal_id),
        }
    elif item_type == "service_account_role":
        assert metadata == {
            "assignment_id": str(assignment_id),
            "role_id": str(target_id),
            "service_account_revision": 7,
        }
    else:
        assert metadata == {
            "target_user_id": str(principal_id),
            "role_id": str(target_id),
            "previous_status": "approved",
        }


def test_domain_revocation_audit_identity_ignores_manual_items():
    item = SimpleNamespace(item_type="legacy_user_role")
    receipt = SimpleNamespace(result_snapshot={})

    assert _domain_revocation_audit_identity(item, receipt) is None

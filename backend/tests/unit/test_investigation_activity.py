from __future__ import annotations

import uuid

from app.services.investigation_activity import (
    evidence_activity_details,
    member_activity_details,
)


def test_activity_display_context_is_bounded_and_keeps_stable_ids():
    source_id = uuid.uuid4()
    member = member_activity_details(
        member_email=f"analyst-{'x' * 400}@example.com",
        role="editor",
    )
    evidence = evidence_activity_details(
        source_type="item",
        source_id=source_id,
        source_title="T" * 700,
    )

    assert member["role"] == "editor"
    assert len(str(member["member_email"])) == 320
    assert str(member["member_email"]).endswith("...")
    assert evidence["source_type"] == "item"
    assert evidence["source_id"] == str(source_id)
    assert len(str(evidence["source_title"])) == 512
    assert str(evidence["source_title"]).endswith("...")


def test_member_activity_context_omits_an_unavailable_email_snapshot():
    assert member_activity_details(member_email=None, role="viewer") == {
        "role": "viewer",
    }

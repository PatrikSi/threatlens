import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.team_assessments import HuntReviewCommand


@pytest.mark.parametrize("note", ["bad\x00text", "bad\udffftext", "x" * 2001])
def test_hunt_review_rejects_unstorable_or_oversized_notes(note):
    with pytest.raises(ValidationError):
        HuntReviewCommand(
            team_id=uuid.uuid4(), expected_version=1, status="accepted", note=note
        )


def test_hunt_review_requires_explicit_version_and_supported_review_decision():
    with pytest.raises(ValidationError):
        HuntReviewCommand(team_id=uuid.uuid4(), status="accepted", note="Checked.")
    with pytest.raises(ValidationError):
        HuntReviewCommand(
            team_id=uuid.uuid4(),
            expected_version=1,
            status="published",
            note="Checked.",
        )
    parsed = HuntReviewCommand(
        team_id=uuid.uuid4(), expected_version=1, status="accepted", note="  Checked.  "
    )
    assert parsed.note == "Checked."


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("restore_quarantine", "stopped after recovery"),
        ("cancel_requested", "canceled"),
        ("private-provider-detail", "was skipped"),
    ],
)
def test_skipped_assessment_has_a_safe_recovery_message(reason, expected):
    from app.services.team_assessment_access import assessment_envelope

    row = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        item_id=uuid.uuid4(),
        version=1,
        context_version=0,
        result_json=None,
        generated_at=None,
    )
    state = SimpleNamespace(
        assessment=row,
        run=SimpleNamespace(status="skipped", reason=reason, error=None),
        result_visible=True,
        error_visible=True,
    )
    actor = SimpleNamespace(authorization=SimpleNamespace(has=lambda permission: True))
    active = SimpleNamespace(
        ai_enabled=True, ai_configured=True, hunt_suggestions_enabled=False
    )
    response = assessment_envelope(state, actor=actor, active=active)
    assert expected in response.assessment.error
    assert "private-provider-detail" not in response.assessment.error

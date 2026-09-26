"""Approval identity follows substantive evidence rather than review annotations."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.hunt_approval import hunt_approval_fingerprint


@pytest.fixture
def approved_hunt():
    row = SimpleNamespace(
        result_context_version=2,
        result_source_version=3,
        result_article_id=uuid4(),
        result_article_retrieved_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )
    hunt = {
        "id": "hunt-one",
        "hypothesis": "An unexpected child process follows the documented exploit.",
        "evidence": [{"source": "article_text", "quote": "The process spawns a shell."}],
        "review_status": "accepted",
        "review_note": "Ready for validation.",
        "reviewed_by_user_id": str(uuid4()),
        "reviewed_at": "2026-09-25T10:00:00+00:00",
    }
    return row, hunt


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewed_by_user_id", "3a01a673-c498-43d4-a1b3-5b5cb6b35329"),
        ("reviewed_at", "2026-09-26T10:00:00+00:00"),
        ("review_note", "A second reviewer confirmed the scope."),
    ],
)
def test_annotation_does_not_change_approval_content(approved_hunt, field, value):
    row, hunt = approved_hunt
    annotated = {**hunt, field: value}
    assert hunt_approval_fingerprint(row, annotated) == hunt_approval_fingerprint(row, hunt)


@pytest.mark.parametrize("field", ["hypothesis", "evidence"])
def test_changed_substantive_content_invalidates_approval(approved_hunt, field):
    row, hunt = approved_hunt
    changed = deepcopy(hunt)
    if field == "evidence":
        changed[field][0]["quote"] = "A different process created the shell."
    else:
        changed[field] = "A different initial access mechanism was observed."
    assert hunt_approval_fingerprint(row, changed) != hunt_approval_fingerprint(row, hunt)


@pytest.mark.parametrize("field", ["result_context_version", "result_source_version"])
def test_changed_evidence_revision_invalidates_approval(approved_hunt, field):
    row, hunt = approved_hunt
    previous = hunt_approval_fingerprint(row, hunt)
    setattr(row, field, getattr(row, field) + 1)
    assert hunt_approval_fingerprint(row, hunt) != previous

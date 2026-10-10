"""Malformed public cursors are client errors, never database or server errors."""

import base64
from datetime import datetime, timezone
import json
from unittest.mock import Mock
import uuid

import pytest

from app.core.api_errors import ApiHTTPException
from app.services import indicator_publications as service


@pytest.fixture()
def listing(monkeypatch):
    db = Mock()
    db.scalars.return_value.all.return_value = []
    actor = object()
    team_id = uuid.uuid4()
    monkeypatch.setattr(service, "fence_indicator_request", Mock())
    monkeypatch.setattr(service, "publication_access", lambda actor: True)
    payload = {
        "team": str(team_id),
        "at": datetime.now(timezone.utc).isoformat(),
        "id": str(uuid.uuid4()),
    }
    return db, actor, team_id, payload


def _cursor(payload):
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


@pytest.mark.parametrize("identity", [{}, [], 123, True, None])
def test_publication_cursor_rejects_non_string_identity(listing, identity):
    db, actor, team_id, payload = listing
    payload["id"] = identity
    with pytest.raises(ApiHTTPException) as raised:
        service.list_publications(
            db, actor=actor, team_id=team_id, cursor=_cursor(payload), limit=20
        )
    assert raised.value.status_code == 400
    assert raised.value.error_code == "publication_cursor_invalid"
    db.scalars.assert_not_called()


@pytest.mark.parametrize("payload", [[], None, 123, {}, {"team": "other"}])
def test_publication_cursor_rejects_invalid_shape_or_scope(listing, payload):
    db, actor, team_id, _ = listing
    with pytest.raises(ApiHTTPException) as raised:
        service.list_publications(
            db, actor=actor, team_id=team_id, cursor=_cursor(payload), limit=20
        )
    assert raised.value.status_code == 400
    db.scalars.assert_not_called()


def test_publication_cursor_round_trip_preserves_valid_position(listing):
    db, actor, team_id, payload = listing
    page = service.list_publications(
        db, actor=actor, team_id=team_id, cursor=_cursor(payload), limit=20
    )
    assert page.items == []
    assert not page.has_more
    assert page.next_cursor is None
    db.scalars.assert_called_once()


def test_publication_cursor_rejects_non_base64_characters(listing):
    db, actor, team_id, payload = listing
    cursor = "!!!!" + _cursor(payload)
    with pytest.raises(ApiHTTPException) as raised:
        service.list_publications(
            db, actor=actor, team_id=team_id, cursor=cursor, limit=20
        )
    assert raised.value.status_code == 400
    db.scalars.assert_not_called()

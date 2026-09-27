"""MCP adapters over real team review, hunt and publication state."""

from copy import deepcopy
from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import delete, event
from app.models.api_token import ApiToken
from app.models.feed import Feed
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.iam import IAMGroupMembership
from app.services.mcp_read_contracts import MCPReadError, json_bytes
from tests.integration.test_data_policy_read_coverage import _enable_enforcement
from tests.integration.test_indicator_intelligence import _command, _extract, _page
from tests.integration.test_indicator_publications import publish
from tests.integration.test_mcp_read_service import _call, _context
from tests.integration.test_team_assessments_api import (
    _queue,
    _ready,
    assessment_setup as setup_fixture,
)
from tests.integration.test_teams_api import _team

assessment_setup = setup_fixture
TOOLS = ("get_indicator_assessments", "get_hunt_queue", "get_reviewed_publications")


@pytest.fixture()
def rich_reads(client, db_session, seed_users, auth_headers, assessment_setup):
    team, item, members, _ = assessment_setup
    db_session.get(Feed, item.feed_id).handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
    db_session.flush()
    _extract(db_session, item)
    db_session.commit()
    row, _ = _ready(
        db_session, _queue(client, assessment_setup, auth_headers["analyst"])
    )
    result = deepcopy(row.result_json)
    result["hunts"].extend(
        {**result["hunts"][0], "id": uuid.uuid4().hex} for _ in range(2)
    )
    row.result_json = result
    db_session.commit()
    indicators = _page(client, assessment_setup, auth_headers["analyst"])
    evil = next(entry for entry in indicators["items"] if entry["value"] == "evil.net")
    response = client.patch(
        f"/items/{item.id}/indicators/{evil['id']}/assessment?team_id={team['id']}",
        json=_command(indicators),
        headers=auth_headers["analyst"],
    )
    assert response.status_code == 200, response.text
    publications = [publish(client, (team, item), auth_headers)[2] for _ in range(3)]
    context = _context(db_session, seed_users["analyst"])
    return {
        "team": team,
        "item": item,
        "members": members,
        "row": row,
        "context": context,
        "publications": publications,
    }


def arguments(setup, tool, **changes):
    args = {"team_id": setup["team"]["id"], "limit": 1}
    if tool == "get_indicator_assessments":
        args["item_id"] = str(setup["item"].id)
    return {**args, **changes}


@pytest.mark.parametrize("tool", TOOLS)
def test_adapter_pages_preserve_all_rows_and_never_write(db_session, rich_reads, tool):
    expected = 2 if tool == "get_indicator_assessments" else 3
    args = arguments(rich_reads, tool)
    seen, statements = [], []
    connection = db_session.connection()

    def capture(_connection, _cursor, statement, *_):
        statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        for _ in range(5):
            page = _call(db_session, rich_reads["context"], tool, args)
            expected_link = {
                "get_indicator_assessments": f"/api/v1/items/{rich_reads['item'].id}/indicators?team_id={rich_reads['team']['id']}",
                "get_hunt_queue": f"/teams?team={rich_reads['team']['id']}&panel=hunts",
                "get_reviewed_publications": f"/api/v1/teams/{rich_reads['team']['id']}/indicator-publications",
            }[tool]
            assert page["canonical_link"] == expected_link
            assert page["provenance"]["content_trust"] == "untrusted_stored_content"
            assert len(page["data"]["items"]) == 1
            entry = page["data"]["items"][0]
            seen.append(
                entry["hunt"]["id"] if tool == "get_hunt_queue" else entry["id"]
            )
            if not page["data"]["has_more"]:
                assert page["next_cursor"] is None
                break
            if tool == "get_indicator_assessments":
                args["page"] = page["data"]["next_page"]
            else:
                assert page["next_cursor"] == page["data"]["next_cursor"]
                args["cursor"] = page["next_cursor"]
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert len(seen) == len(set(seen)) == expected
    assert all(
        statement.lstrip().split()[0].upper() == "SELECT" for statement in statements
    )


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("change", ["membership", "token"])
def test_adapter_rechecks_current_membership_and_token(
    db_session, rich_reads, tool, change, seed_users
):
    args = arguments(rich_reads, tool)
    assert _call(db_session, rich_reads["context"], tool, args)["data"]["items"]
    if change == "membership":
        db_session.execute(
            delete(IAMGroupMembership).where(
                IAMGroupMembership.group_id == rich_reads["members"].id,
                IAMGroupMembership.user_id == seed_users["analyst"].id,
            )
        )
    else:
        token = db_session.get(
            ApiToken, rich_reads["context"].credential_snapshot.credential_id
        )
        token.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    with pytest.raises(MCPReadError) as failure:
        _call(db_session, rich_reads["context"], tool, args)
    assert failure.value.code in {"not_found", "access_denied"}


@pytest.mark.parametrize("tool", TOOLS)
def test_adapter_hides_other_teams_like_unknown_teams(
    client, db_session, auth_headers, seed_users, rich_reads, tool
):
    other, members, _ = _team(client, db_session, seed_users, auth_headers)
    db_session.execute(
        delete(IAMGroupMembership).where(
            IAMGroupMembership.group_id == members.id,
            IAMGroupMembership.user_id == seed_users["analyst"].id,
        )
    )
    db_session.commit()
    errors = []
    for identifier in (other["id"], str(uuid.uuid4())):
        with pytest.raises(MCPReadError) as failure:
            _call(
                db_session,
                rich_reads["context"],
                tool,
                arguments(rich_reads, tool, team_id=identifier),
            )
        errors.append((failure.value.code, failure.value.message))
    assert errors[0] == errors[1]


@pytest.mark.parametrize("tool", TOOLS)
def test_adapter_current_evidence_policy_withholds_relabelled_sources(
    db_session, rich_reads, tool, seed_users, monkeypatch
):
    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    context = _context(db_session, seed_users["analyst"])
    args = arguments(rich_reads, tool)
    assert _call(db_session, context, tool, args)["data"]["items"]
    db_session.get(Feed, rich_reads["item"].feed_id).handling_label_id = restricted.id
    db_session.commit()
    if tool == "get_indicator_assessments":
        with pytest.raises(MCPReadError) as failure:
            _call(db_session, context, tool, args)
        assert failure.value.code == "not_found"
    else:
        assert _call(db_session, context, tool, args)["data"]["items"] == []


def test_indicator_tool_without_team_scope_omits_team_verdicts(
    db_session, rich_reads, seed_users
):
    limited = _context(
        db_session, seed_users["analyst"], scopes=["read:mcp", "read:items"]
    )
    page = _call(
        db_session,
        limited,
        "get_indicator_assessments",
        {"item_id": str(rich_reads["item"].id)},
    )
    assert page["data"]["items"]
    assert all(
        entry["assessment"] is None and not entry["suppressed"]
        for entry in page["data"]["items"]
    )
    with pytest.raises(MCPReadError) as failure:
        _call(
            db_session,
            limited,
            "get_indicator_assessments",
            arguments(rich_reads, "get_indicator_assessments"),
        )
    assert failure.value.code == "access_denied"


@pytest.mark.parametrize("tool", ["get_hunt_queue", "get_reviewed_publications"])
def test_team_cursor_rechecks_access_and_rejects_corruption(
    db_session, rich_reads, tool
):
    args = arguments(rich_reads, tool)
    first = _call(db_session, rich_reads["context"], tool, args)
    assert first["next_cursor"]
    with pytest.raises(MCPReadError):
        _call(
            db_session,
            rich_reads["context"],
            tool,
            {**args, "team_id": str(uuid.uuid4()), "cursor": first["next_cursor"]},
        )
    with pytest.raises(MCPReadError):
        _call(db_session, rich_reads["context"], tool, {**args, "cursor": "corrupted"})


@pytest.mark.parametrize("tool", TOOLS)
def test_adapter_byte_cap_never_returns_partial_rows_with_a_cursor(
    db_session, rich_reads, tool
):
    args = arguments(rich_reads, tool, limit=3)
    full = _call(db_session, rich_reads["context"], tool, args)
    # Keep metadata itself within budget while forcing at least one data row
    # to shrink. The adapter must reject a partial page, not skip its tail.
    base = "https://example.test/" + "x" * max(0, 2200 - len(json_bytes(full)))
    with pytest.raises(MCPReadError) as failure:
        _call(
            db_session,
            rich_reads["context"],
            tool,
            args,
            canonical_base_url=base,
            max_response_bytes=2048,
        )
    assert failure.value.code == "response_too_large"
    assert "smaller limit" in failure.value.message
    retry = _call(db_session, rich_reads["context"], tool, {**args, "limit": 1})
    assert len(retry["data"]["items"]) == 1
    assert len(json_bytes(retry)) <= 65536
    assert full["data"]["items"][0] == retry["data"]["items"][0]

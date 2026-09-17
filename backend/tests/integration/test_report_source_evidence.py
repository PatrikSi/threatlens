"""Retained evidence is bounded, permission checked and pinned across pages."""

import pytest
import uuid
from sqlalchemy import event, select

from app.models.report_source_item import ReportSourceItem
from tests.integration.test_report_editorial import draft as draft


def _read(client, headers, report, **params):
    return client.get(
        f"/reports/{report.id}/sources/S1/evidence",
        headers=headers,
        params={"editorial_version": report.editorial_version, **params},
    )


def test_retained_source_pages_reassemble_exact_text_without_full_body_select(
    client, db_session, auth_headers, draft,
):
    source = db_session.scalar(select(ReportSourceItem).where(ReportSourceItem.report_id == draft.id))
    passage = "Retained 🧪 evidence\n" * 1200
    source.evidence_text = passage
    db_session.commit()
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    try:
        first = _read(client, auth_headers["analyst"], draft, limit=8000)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)
    assert first.status_code == 200, first.text
    assert first.headers["Cache-Control"] == "no-store"
    page = first.json()
    assert page["evidence_text"] == passage[:8000]
    assert page["total_characters"] == len(passage)
    assert any("substr(coalesce(report_source_items.evidence_text" in statement for statement in statements)
    output = page["evidence_text"]
    while page["next_offset"] is not None:
        response = _read(client, auth_headers["analyst"], draft, offset=page["next_offset"], source_revision=page["source_revision"])
        assert response.status_code == 200, response.text
        page = response.json()
        output += page["evidence_text"]
    assert output == passage


def test_retained_source_requires_matching_report_and_passage_revisions(client, db_session, auth_headers, draft):
    first = _read(client, auth_headers["analyst"], draft, limit=2).json()
    assert _read(client, auth_headers["analyst"], draft, offset=2).status_code == 422
    source = db_session.scalar(select(ReportSourceItem).where(ReportSourceItem.report_id == draft.id))
    source.evidence_text = "Updated retained text."
    db_session.commit()
    changed = _read(client, auth_headers["analyst"], draft, offset=2, source_revision=first["source_revision"])
    assert changed.status_code == 409
    assert "changed" in changed.json()["detail"].lower()
    assert _read(client, auth_headers["analyst"], draft, editorial_version=draft.editorial_version + 1).status_code == 409


def test_legacy_source_without_retained_passage_is_explicitly_empty(client, db_session, auth_headers, draft):
    source = db_session.scalar(select(ReportSourceItem).where(ReportSourceItem.report_id == draft.id))
    source.evidence_text = ""
    db_session.commit()
    response = _read(client, auth_headers["viewer"], draft)
    assert response.status_code == 200, response.text
    assert response.json()["evidence_text"] == ""
    assert response.json()["total_characters"] == 0
    assert response.json()["next_offset"] is None


def test_retained_source_distinguishes_end_of_text_from_invalid_offset(client, auth_headers, draft):
    first = _read(client, auth_headers["analyst"], draft).json()
    end = first["total_characters"]
    revision = first["source_revision"]

    complete = _read(client, auth_headers["analyst"], draft, offset=end, source_revision=revision)
    assert complete.status_code == 200, complete.text
    assert complete.json()["evidence_text"] == ""
    assert complete.json()["next_offset"] is None

    beyond = _read(client, auth_headers["analyst"], draft, offset=end + 1, source_revision=revision)
    assert beyond.status_code == 422, beyond.text
    assert beyond.json()["error"]["code"] == "report_evidence_offset_invalid"


def test_retained_source_reports_a_missing_citation_without_substituting_other_evidence(client, auth_headers, draft):
    response = client.get(
        f"/reports/{draft.id}/sources/S999/evidence",
        headers=auth_headers["analyst"],
        params={"editorial_version": draft.editorial_version},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "report_evidence_not_found"
    assert "evidence_text" not in response.json()


@pytest.mark.parametrize("params", [{"limit": 16001}, {"offset": -1}, {"source_revision": "invalid"}])
def test_retained_source_rejects_invalid_page_arguments(client, auth_headers, draft, params):
    assert _read(client, auth_headers["analyst"], draft, **params).status_code == 422


def test_retained_source_uses_parent_report_visibility(client, db_session, auth_headers, seed_users, draft, monkeypatch):
    from app.services.data_access_envelopes import DataAccessSourceInput, put_data_access_envelope_sources
    from tests.integration.test_data_policy_read_coverage import _enable_enforcement

    restricted = _enable_enforcement(db_session, seed_users, monkeypatch)
    put_data_access_envelope_sources(
        db_session, resource_type="report", resource_id=draft.id,
        sources=[DataAccessSourceInput(
            source_type="item", source_id=str(uuid.uuid4()), source_version="1",
            handling_label_id=restricted.id, captured_policy_revision=2,
        )],
    )
    db_session.commit()
    assert _read(client, auth_headers["analyst"], draft).status_code == 404

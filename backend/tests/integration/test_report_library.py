from dataclasses import replace
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import event

from app.api.deps import get_data_access_context
from app.main import app
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.report import Report
from app.schemas.report_library import ReportLibraryFilters
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_REPORT, DataAccessSourceInput, merge_data_access_envelope_sources,
)
from app.services.data_access_policy import DataAccessContext
from app.services.report_library import ReportLibraryCursorError, list_report_library_page


def _context(**changes):
    return replace(DataAccessContext(mode="off", policy_revision=1, coverage_version=1,
        principal_type="user", principal_id=uuid.UUID(int=1), principal_eligible=True,
        allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID})), **changes)


def _seed(db, titles, **changes):
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = []
    for index, title in enumerate(titles):
        values = dict(id=uuid.UUID(int=index + 100), title=title, report_type="custom",
                      status="ready", created_at=when, period_start=when - timedelta(days=1),
                      period_end=when)
        rows.append(Report(**(values | changes)))
    db.add_all(rows)
    db.flush()
    return rows


def _page(db, **kwargs):
    defaults = dict(filters=ReportLibraryFilters(), data_access=_context())
    return list_report_library_page(db, **(defaults | kwargs))


def test_keysets_reach_all_rows_and_survive_ties_insertions_and_deleted_anchor(db_session):
    rows = _seed(db_session, [f"Report {i}" for i in range(127)])
    first = _page(db_session)
    assert [item.id for item in first.items] == [row.id for row in reversed(rows[-25:])]
    anchor_id = first.items[-1].id
    # An ordinary concurrent insert is newer than the first-page cutoff.
    new = _seed(db_session, ["New arrival"], id=uuid.uuid4(), created_at=first.as_of + timedelta(microseconds=1))[0]
    db_session.delete(db_session.get(Report, anchor_id))
    db_session.flush()
    seen = [item.id for item in first.items]
    cursor = first.next_cursor
    while cursor:
        page = _page(db_session, cursor=cursor)
        assert page.as_of == first.as_of
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
    assert len(seen) == 127 and len(set(seen)) == 127
    assert new.id not in seen
    assert seen == [row.id for row in reversed(rows)]
    back = _page(db_session, cursor=first.current_cursor)
    assert new.id not in {item.id for item in back.items}
    assert anchor_id not in {item.id for item in back.items}
    assert _page(db_session).items[0].id == new.id


@pytest.mark.parametrize(("query", "expected"), [
    ("ransomware campaign", ["Ransomware campaign alpha"]),
    ('"supply chain"', ["Supply chain compromise"]),
    ("phishing OR ransomware", ["Phishing operations", "Ransomware campaign alpha"]),
    ("campaign -alpha", ["Supply campaign chain"]),
    ("00000000-0000-0000-0000-000000000064", ["Ransomware campaign alpha"]),
    ('" odd punctuation ((( ', []),
])
def test_search_words_phrases_alternatives_exclusions_and_exact_id(db_session, query, expected):
    _seed(db_session, ["Ransomware campaign alpha", "Supply chain compromise", "Phishing operations", "Supply campaign chain"])
    page = _page(db_session, filters=ReportLibraryFilters(q=query))
    assert [item.title for item in page.items] == expected


def test_filters_use_exact_values_and_half_open_utc_dates(db_session):
    start = datetime.now(timezone.utc) - timedelta(days=2)
    _seed(db_session, ["Included"], created_at=start, report_type="weekly", trigger_source="scheduled")
    _seed(db_session, ["Upper boundary"], id=uuid.uuid4(), created_at=start + timedelta(days=1),
          report_type="weekly", trigger_source="scheduled")
    _seed(db_session, ["Other trigger"], id=uuid.uuid4(), created_at=start, report_type="weekly")
    filters = ReportLibraryFilters(status="ready", report_type="weekly", trigger_source="scheduled",
                                  created_from=start, created_before=start + timedelta(days=1))
    assert [item.title for item in _page(db_session, filters=filters).items] == ["Included"]


def test_cursor_rechecks_current_permissions_and_rejects_changed_principal_or_filter(db_session):
    rows = _seed(db_session, ["Visible one", "Visible two", "No envelope"])
    for row in rows[:2]:
        merge_data_access_envelope_sources(db_session, resource_type=DATA_ACCESS_RESOURCE_REPORT,
            resource_id=row.id, sources=[DataAccessSourceInput(source_type="item", source_id=str(uuid.uuid4()),
                source_version="v1", handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID, captured_policy_revision=1)])
    db_session.flush()
    context = _context(mode="enforced")
    first = _page(db_session, data_access=context, limit=1)
    assert first.items[0].id == rows[1].id
    assert first.next_cursor
    denied = _page(db_session, data_access=replace(context, allowed_label_ids=frozenset()), cursor=first.next_cursor)
    assert denied.items == []
    assert _page(db_session, data_access=replace(context, principal_eligible=False), cursor=first.current_cursor).items == []
    with pytest.raises(ReportLibraryCursorError):
        _page(db_session, cursor=first.next_cursor, data_access=replace(context, principal_id=uuid.uuid4()))
    with pytest.raises(ReportLibraryCursorError):
        _page(db_session, cursor=first.next_cursor, data_access=context, filters=ReportLibraryFilters(q="Visible"))


def test_library_projection_does_not_materialize_body_or_configuration(db_session):
    _seed(db_session, ["Large report"], summary_text="S" * 1_000_000,
          generation_context_json={"large": "C" * 1_000_000}, error="E" * 50_000)
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().startswith("SELECT"):
            statements.append(statement)
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        page = _page(db_session)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert len(page.items[0].error) == 4000
    assert len(statements) == 1
    assert "summary_text" not in statements[0] and "generation_context_json" not in statements[0]
    assert "LIMIT" in statements[0] and "OFFSET" not in statements[0]
    assert len(page.model_dump_json()) < 6000


def test_library_route_validates_scope_and_preserves_legacy_api(client, db_session, auth_headers):
    _seed(db_session, ["Published title", "Second title"])
    app.dependency_overrides[get_data_access_context] = lambda: _context()
    headers = auth_headers["viewer"]
    response = client.get("/reports/library?limit=1", headers=headers)
    assert response.status_code == 200
    page = response.json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert client.get("/reports/library", params={"cursor": page["next_cursor"], "q": "different"}, headers=headers).status_code == 422
    for params in ({"cursor": "not%base64"}, {"cursor": "e30"}, {"limit": 101}, {"status": "bogus"},
                   {"trigger_source": "bogus"}, {"q": "x" * 201}, {"created_from": "2026-09-02", "created_before": "2026-09-01"}):
        assert client.get("/reports/library", params=params, headers=headers).status_code == 422
    legacy = client.get("/reports?limit=1&offset=1", headers=headers)
    assert legacy.status_code == 200 and isinstance(legacy.json(), list)

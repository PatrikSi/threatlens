from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex

from app.models.alert_occurrence import AlertOccurrence
from app.models.report import Report


def _index(table, name: str):
    return next(candidate for candidate in table.indexes if candidate.name == name)


def test_report_observed_at_index_matches_candidate_time_expression():
    index = _index(Report.__table__, "ix_reports_observed_at")

    postgres_sql = str(CreateIndex(index).compile(dialect=postgresql.dialect())).lower()
    sqlite_sql = str(CreateIndex(index).compile(dialect=sqlite.dialect())).lower()

    assert "coalesce(generated_at, created_at)" in postgres_sql
    assert "coalesce(generated_at, created_at)" in sqlite_sql


def test_alert_owner_created_index_matches_candidate_scope_and_sort():
    index = _index(
        AlertOccurrence.__table__,
        "ix_alert_occurrences_owner_created",
    )

    assert [expression.name for expression in index.expressions] == [
        "owner_user_id",
        "created_at",
    ]

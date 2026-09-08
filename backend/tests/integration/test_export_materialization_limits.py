"""Check every export payload query against changes after initial preflight."""

import uuid
from dataclasses import replace

import pytest
from sqlalchemy import event, inspect, update

from app.models.article import Article
from app.models.ioc import IOC, ItemIOC
from app.models.tag import Tag, ItemTag
from app.schemas.exports import ArticleExportOptions
from app.services import export_pdf, export_query
from app.services.export_artifacts import ExportSizeLimitError
from tests.integration.test_export_materialization import _seed_sources


def test_unused_raw_ioc_is_not_materialized(db_session, seed_users):
    ids, context, _, _ = _seed_sources(db_session, seed_users["analyst"])
    ioc = IOC(
        type="domain",
        value_norm=f"{uuid.uuid4().hex}.example",
        value_raw="x" * 9_000_000,
    )
    db_session.add(ioc)
    db_session.flush()
    db_session.add(ItemIOC(item_id=ids[0], ioc_id=ioc.id))
    db_session.flush()
    db_session.expunge_all()
    loaded = []

    def record_raw(value, _context):
        loaded.append("value_raw" in inspect(value).unloaded)

    event.listen(IOC, "load", record_raw)
    try:
        records = list(
            export_query.iter_export_records(
                db_session, item_ids=ids, context=context, include_iocs=True
            )
        )
    finally:
        event.remove(IOC, "load", record_raw)
    assert len(records) == len(records[0].iocs) == 1
    assert loaded == [True]


@pytest.mark.parametrize("field", ["content_type", "extraction_method", "language"])
def test_unbounded_article_metadata_is_budgeted_before_materialization(
    db_session, seed_users, monkeypatch, field
):
    ids, context, _, _ = _seed_sources(db_session, seed_users["analyst"])
    db_session.execute(
        update(Article)
        .where(Article.item_id == ids[0])
        .values(**{field: "x" * 9_000_000})
    )
    db_session.flush()

    def unexpected_load(*_args, **_kwargs):
        pytest.fail("oversized metadata must not reach the materializing query")

    monkeypatch.setattr(export_query, "_load_export_record_batch", unexpected_load)
    with pytest.raises(ExportSizeLimitError, match="record budget"):
        list(
            export_query.iter_export_records(
                db_session, item_ids=ids, context=context, include_iocs=True
            )
        )


@pytest.mark.parametrize("association", ["tags", "iocs"])
def test_association_growth_is_rejected_in_the_materializing_statement(
    db_session, seed_users, monkeypatch, association
):
    ids, context, _, _ = _seed_sources(db_session, seed_users["analyst"])
    monkeypatch.setattr(export_query, "EXPORT_RECORD_BATCH_MAX_BYTES", 5000)
    loader_name = (
        "_load_export_tags_for_items"
        if association == "tags"
        else "_load_iocs_for_items"
    )
    original = getattr(export_query, loader_name)

    def grow_associations(db, **kwargs):
        for _ in range(50):
            if association == "tags":
                entity = Tag(name=f"review-{uuid.uuid4().hex}")
                db.add(entity)
                db.flush()
                db.add(ItemTag(item_id=ids[0], tag_id=entity.id, source="rule"))
            else:
                entity = IOC(
                    type="domain",
                    value_norm=f"{uuid.uuid4().hex}.example",
                    value_raw="example",
                )
                db.add(entity)
                db.flush()
                db.add(ItemIOC(item_id=ids[0], ioc_id=entity.id))
        db.flush()
        return original(db, **kwargs)

    monkeypatch.setattr(export_query, loader_name, grow_associations)
    returned = []

    def record_rows(_connection, cursor, statement, _parameters, _context, _many):
        if f"LEFT OUTER JOIN {association}" in statement:
            returned.append(cursor.rowcount)

    connection = db_session.connection()
    event.listen(connection, "after_cursor_execute", record_rows)
    try:
        with pytest.raises(export_query.ExportSnapshotChangedError):
            list(
                export_query.iter_export_records(
                    db_session, item_ids=ids, context=context, include_iocs=True
                )
            )
    finally:
        event.remove(connection, "after_cursor_execute", record_rows)
    assert returned == [0], (
        "the oversized child payload must never cross the database boundary"
    )


def test_pdf_distinguishes_omitted_text_from_missing_text(
    db_session, seed_users, monkeypatch
):
    ids, context, _, _ = _seed_sources(db_session, seed_users["analyst"])
    record = next(
        export_query.iter_export_records(
            db_session,
            item_ids=ids,
            context=context,
            include_iocs=False,
            text_projection=export_query.ExportTextProjection(
                include_article_text=False
            ),
        )
    )
    sections = []
    original = export_pdf._section

    def collect_section(title, body, **kwargs):
        sections.append((title, body))
        return original(title, body, **kwargs)

    monkeypatch.setattr(export_pdf, "_section", collect_section)
    output = export_pdf.build_article_pdf(
        record, options=ArticleExportOptions(pdf_include_article_text=False)
    )
    assert output.startswith(b"%PDF-")
    assert not any("No full article text is available" in body for _, body in sections)
    sections.clear()
    missing = replace(record, article=replace(record.article, text_available=False))
    export_pdf.build_article_pdf(
        missing, options=ArticleExportOptions(pdf_include_article_text=False)
    )
    assert any("No full article text is available" in body for _, body in sections)

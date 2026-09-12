"""Prove source freshness and bounded materialization through PostgreSQL queries."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.models.tag import ItemTag, Tag
from app.services import ai_integration
from app.services.ai_execution_ownership import ai_worker_execution
from app.services.ai_ops import queue_ai_task_run
from app.services.ai_brief_sources import (
    MAX_AUDIT_ROWS,
    MAX_SOURCE_BYTES,
    METADATA_ROW_BYTES,
    load_brief_sources,
)
from app.services.ai_config import load_active_ai_settings
from app.services.ai_enrichment_provenance import (
    STALE_ENRICHMENT_WARNING,
    current_enrichment_predicate,
)
from app.services.ai_reporting import daily_brief_response_from_model
from app.services.export_models import ExportRecord, ExportUserState
from app.services.export_query import _serialize_ai
from app.services.report_sources import _build_evidence_text
from tests.integration.test_ai_feature_output_validation import (
    configured_item as configured_item,
    _provider,
    _run,
)


def _current(db, item_id):
    return bool(
        db.scalar(
            select(current_enrichment_predicate())
            .select_from(Item)
            .join(Feed, Feed.id == Item.feed_id)
            .outerjoin(Article, Article.item_id == Item.id)
            .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
            .outerjoin(ItemAIEnrichment, ItemAIEnrichment.item_id == Item.id)
            .where(Item.id == item_id)
        )
    )


def _sources(db, *, count=1, audit_limit=500):
    now = datetime.now(timezone.utc)
    return load_brief_sources(
        db,
        active=load_active_ai_settings(db, feature_type="daily_brief"),
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(seconds=1),
        total_items=count,
        audit_limit=audit_limit,
    )


def _success(db, item, monkeypatch):
    sent = _provider(
        monkeypatch,
        [{"summary_text": "The affected version is 1.0.", "relevance_score": 0.9}],
    )
    _run_row, result, resource = _run(db, feature="item_enrichment", item=item)
    assert len(sent) == 1 and result.status == "ready"
    assert _current(db, item.id)
    return resource


def test_failed_refresh_keeps_successful_provenance_but_brief_uses_primary_evidence(
    db_session, configured_item, monkeypatch
):
    item, _settings = configured_item
    resource = _success(db_session, item, monkeypatch)
    successful_proof = dict(resource.result_provenance_json)
    item.summary = "The affected version is 2.0."
    item.classification_required_version += 1
    db_session.commit()
    _provider(monkeypatch, [{}])
    _run_row, result, resource = _run(db_session, feature="item_enrichment", item=item)
    assert result.status == "error"
    assert resource.result_provenance_json == successful_proof
    assert resource.source_hash != successful_proof["source_hash"]
    assert resource.summary_text == "The affected version is 1.0."
    selected = _sources(db_session)
    assert selected.selected_rows[0].ai_summary is None
    assert selected.selected_rows[0].summary == "The affected version is 2.0."
    assert selected.selected_rows[0].relevance_score is None
    assert selected.warnings == [STALE_ENRICHMENT_WARNING]
    _provider(monkeypatch, [{"brief_text": "The affected version is 2.0."}])
    _run_row, result, brief = _run(db_session, feature="daily_brief", item=item)
    assert result.status == "ready"
    assert daily_brief_response_from_model(db_session, brief).evidence_warnings == [
        STALE_ENRICHMENT_WARNING
    ]


@pytest.mark.parametrize(
    "change",
    [
        "source_version",
        "article_revision",
        "classification",
        "tags",
        "feed",
        "url",
        "published_at",
        "unknown_provenance",
    ],
)
def test_source_changes_invalidate_summary_reuse(
    db_session, configured_item, monkeypatch, change
):
    item, _settings = configured_item
    resource = _success(db_session, item, monkeypatch)
    if change == "source_version":
        item.classification_required_version += 1
    elif change == "article_revision":
        db_session.scalar(
            select(Article).where(Article.item_id == item.id)
        ).retrieved_at += timedelta(microseconds=1)
    elif change == "classification":
        db_session.add(
            ItemClassification(
                item_id=item.id, primary_category="vulnerability", source_hash="b" * 64
            )
        )
    elif change == "tags":
        tag = Tag(id=uuid.uuid4(), name="New evidence")
        db_session.add(tag)
        db_session.flush()
        db_session.add(ItemTag(item_id=item.id, tag_id=tag.id))
    elif change == "feed":
        db_session.get(Feed, item.feed_id).name = "New feed name"
    elif change == "url":
        item.canonical_url = "https://example.com/corrected"
    elif change == "published_at":
        item.published_at += timedelta(seconds=1)
    else:
        resource.result_provenance_json = {
            "version": 1,
            "source_version": {"malformed": "numeric"},
        }
    db_session.commit()
    assert not _current(db_session, item.id)
    assert _sources(db_session).selected_rows[0].ai_summary is None


def test_unchanged_source_can_refresh_verified_provenance_without_provider_io(
    db_session, configured_item, monkeypatch
):
    item, _settings = configured_item
    resource = _success(db_session, item, monkeypatch)
    generated_at = resource.result_provenance_json["generated_at"]
    article = db_session.scalar(select(Article).where(Article.item_id == item.id))
    article.retrieved_at += timedelta(seconds=1)
    db_session.commit()
    assert not _current(db_session, item.id)
    monkeypatch.setattr(
        ai_integration,
        "_request_json_with_usage",
        lambda *args, **kwargs: pytest.fail("unchanged content must not send"),
    )
    result = ai_integration.run_item_ai_enrichment(db_session, item_id=item.id)
    db_session.commit()
    assert result.reason == "source_hash_unchanged" and _current(db_session, item.id)
    assert resource.result_provenance_json["generated_at"] == generated_at


def test_non_ascii_tags_have_identical_python_and_sql_provenance(
    db_session, configured_item, monkeypatch
):
    item, _settings = configured_item
    for name in ["Zebra", "Álpha", "évidence", "alpha", "三", "😀"]:
        tag = Tag(id=uuid.uuid4(), name=name)
        db_session.add(tag)
        db_session.flush()
        db_session.add(ItemTag(item_id=item.id, tag_id=tag.id))
    db_session.commit()
    _success(db_session, item, monkeypatch)
    assert (
        _sources(db_session).selected_rows[0].ai_summary
        == "The affected version is 1.0."
    )


def test_superseded_worker_cannot_refresh_cached_result_provenance(
    db_session,
    configured_item,
    monkeypatch,
):
    item, _settings = configured_item
    enrichment = _success(db_session, item, monkeypatch)
    saved_proof = dict(enrichment.result_provenance_json)
    article = db_session.scalar(select(Article).where(Article.item_id == item.id))
    article.retrieved_at += timedelta(seconds=1)
    run = queue_ai_task_run(
        db_session,
        task_type="item_enrichment",
        trigger_source="manual",
        item_id=item.id,
    )
    run.status, run.celery_task_id = "running", "replacement"
    db_session.commit()
    monkeypatch.setattr(
        ai_integration,
        "_request_json_with_usage",
        lambda *args, **kwargs: pytest.fail("cached evidence must not send"),
    )

    @ai_worker_execution
    def stale_worker(task, task_run_id):
        return ai_integration.run_item_ai_enrichment(
            db_session, item_id=item.id, task_run_id=run.id
        )

    result = stale_worker(
        SimpleNamespace(request=SimpleNamespace(id="original")), str(run.id)
    )
    db_session.commit()
    assert result.reason == "superseded_delivery"
    assert enrichment.result_provenance_json == saved_proof
    assert not _current(db_session, item.id)
    assert run.status == "running" and run.celery_task_id == "replacement"


def test_brief_does_not_materialize_audit_only_bodies(db_session, configured_item):
    item, settings = configured_item
    settings.daily_brief_max_items = 5
    item.summary = "x" * 100_000
    for index in range(39):
        row = Item(
            id=uuid.uuid4(),
            feed_id=item.feed_id,
            url=f"https://example.com/{index}",
            title=f"Synthetic {index}",
            summary="x" * 100_000,
            published_at=item.published_at,
            dedupe_key=f"review-{index}",
            content_hash="b" * 64,
        )
        db_session.add(row)
        db_session.flush()
        db_session.add(
            ItemAIEnrichment(
                item_id=row.id,
                status="ready",
                source_hash="a" * 64,
                summary_text="y" * 100_000,
            )
        )
    db_session.commit()
    selected = _sources(db_session, count=40)
    assert len(selected.audit_rows) == 40 and len(selected.selected_rows) == 5
    assert (
        sum(
            len(row.summary or "") + len(row.ai_summary or "")
            for row in selected.audit_rows
        )
        == 4500
    )
    assert all(
        row.summary is None and row.ai_summary is None
        for row in selected.audit_rows[5:]
    )
    assert MAX_AUDIT_ROWS * METADATA_ROW_BYTES + 100 * 2 * 900 * 4 <= MAX_SOURCE_BYTES


@pytest.mark.parametrize("summary", [None, "  \n  ", "\u00a0\u2003\u2028"])
def test_brief_primary_fallback_bounds_unicode_metadata_and_omits_overlong_urls(
    db_session, configured_item, summary,
):
    item, _settings = configured_item
    item.summary, item.title = summary, "😀" * 1000
    item.url = "https://example.com/" + "x" * 5000
    article = db_session.scalar(select(Article).where(Article.item_id == item.id))
    article.text = "Primary evidence. " * 1000
    db_session.commit()
    selected = _sources(db_session).selected_rows[0]
    assert selected.summary == article.text[:900]
    assert selected.title == "😀" * 512
    assert selected.url is None


def test_report_primary_evidence_precedes_verified_ai_and_excludes_stale_output(
    db_session, configured_item, monkeypatch
):
    item, _settings = configured_item
    resource = _success(db_session, item, monkeypatch)

    def record(current):
        return ExportRecord(
            id=item.id,
            feed_id=item.feed_id,
            feed_name="Synthetic",
            source_guid=None,
            url=item.url,
            canonical_url=item.canonical_url,
            title=item.title,
            summary="Primary publisher evidence.",
            published_at=item.published_at,
            first_seen_at=item.first_seen_at,
            status=item.status,
            classification=None,
            ai=_serialize_ai(
                resource, summary=resource.summary_text, source_current=current
            ),
            article=None,
            state=ExportUserState(False, False, None, None),
        )

    evidence = _build_evidence_text(record(True), citation_key="S1")
    assert evidence.index("Primary publisher evidence.") < evidence.index(
        "The affected version is 1.0."
    )
    assert "Existing grounded summary" not in evidence
    assert "The affected version is 1.0." not in _build_evidence_text(
        record(False), citation_key="S1"
    )

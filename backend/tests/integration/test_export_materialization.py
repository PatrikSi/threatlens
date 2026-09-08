"""Exercise database projection and byte guards using real large source rows."""

import uuid
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, update

from app.api.routes import exports as exports_route
from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.schemas.exports import ArticleExportFilters
from app.schemas.reports import ReportPromptConfig, ReportSectionConfig
from app.services import export_query, report_sources
from app.services.ai_config import load_active_ai_settings
from app.services.ai_context_budget import AIContextBudgetError, estimate_tokens
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization
from app.services.export_artifacts import ExportSizeLimitError


@pytest.fixture(autouse=True)
def _isolated_export_lock(monkeypatch):
    monkeypatch.setattr(exports_route, "acquire_export_lock", lambda **kwargs: nullcontext())


def _seed_sources(db, user, *, count=1, body="Full extracted text", summary="Summary"):
    feed = Feed(name="Byte budget feed", url=f"https://example.com/{uuid.uuid4()}")
    db.add(feed)
    db.flush()
    ids = []
    for index in range(count):
        item = Item(
            feed_id=feed.id,
            title=f"Security evidence {index}",
            url=f"https://example.com/source/{uuid.uuid4()}",
            summary=summary,
            dedupe_key=str(uuid.uuid4()),
            content_hash="a" * 64,
            status="content_fetched",
            first_seen_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.flush()
        ids.append(item.id)
        db.add(Article(item_id=item.id, final_url=item.url, http_status=200, text=body))
        db.add(
            ItemAIEnrichment(
                item_id=item.id,
                status="completed",
                source_hash="b" * 64,
                summary_text=summary,
                relevance_reasons_json=[],
            )
        )
    db.commit()
    access = data_access_context_for_authorization(
        db, authorization_context_for_user(db, user)
    )
    filters = ArticleExportFilters(feed_ids=[feed.id])
    context = export_query.build_export_query_context(
        user_id=user.id, filters=filters, data_access=access
    )
    db.expunge_all()
    return ids, context, filters, access


def test_preview_projection_omits_large_bodies_but_preserves_text_availability(
    db_session, seed_users
):
    ids, context, _, _ = _seed_sources(
        db_session,
        seed_users["analyst"],
        body="x" * 9_000_000,
        summary="s" * 5_000_000,
    )
    records = list(
        export_query.iter_export_records(
            db_session,
            item_ids=ids,
            context=context,
            include_iocs=True,
            text_projection=export_query.ExportTextProjection(
                include_article_text=False, include_summaries=False
            ),
        )
    )
    assert records[0].article.text is None
    assert records[0].summary is None
    assert records[0].ai.summary is None
    assert export_query.build_preview_items(records)[0].has_article_text is True


def test_full_text_exports_batch_by_encoded_bytes_without_truncation(
    db_session, seed_users, monkeypatch
):
    body = "é" * 3000
    ids, context, _, _ = _seed_sources(
        db_session, seed_users["analyst"], count=6, body=body
    )
    monkeypatch.setattr(export_query, "EXPORT_RECORD_BATCH_MAX_BYTES", 18_000)
    original = export_query._load_export_record_batch
    batches = []

    def load(db, **kwargs):
        records = original(db, **kwargs)
        batches.append(len(records))
        assert (
            sum(len(record.article.text.encode()) for record in records.values())
            <= 18_000
        )
        return records

    monkeypatch.setattr(export_query, "_load_export_record_batch", load)
    records = list(
        export_query.iter_export_records(
            db_session, item_ids=ids, context=context, include_iocs=True
        )
    )
    assert len(records) == 6
    assert all(record.article.text == body for record in records)
    assert len(batches) >= 3


def test_oversized_source_is_rejected_before_body_materialization(
    db_session, seed_users, monkeypatch
):
    ids, context, _, _ = _seed_sources(
        db_session, seed_users["analyst"], body="x" * 9_000_000
    )

    def unexpected_load(*args, **kwargs):
        pytest.fail("oversized body must not reach the materializing query")

    monkeypatch.setattr(export_query, "_load_export_record_batch", unexpected_load)
    with pytest.raises(ExportSizeLimitError, match="record budget"):
        list(
            export_query.iter_export_records(
                db_session, item_ids=ids, context=context, include_iocs=True
            )
        )


def test_growth_between_preflight_and_load_aborts_snapshot(
    db_session, seed_users, monkeypatch
):
    ids, context, _, _ = _seed_sources(db_session, seed_users["analyst"])
    original = export_query._load_export_record_batch

    def grow_before_load(db, **kwargs):
        db.execute(
            update(Article)
            .where(Article.item_id == ids[0])
            .values(text="x" * 9_000_000)
        )
        db.flush()
        return original(db, **kwargs)

    monkeypatch.setattr(export_query, "_load_export_record_batch", grow_before_load)
    with pytest.raises(export_query.ExportSnapshotChangedError):
        list(
            export_query.iter_export_records(
                db_session, item_ids=ids, context=context, include_iocs=True
            )
        )
    assert not any(
        isinstance(value, Article) for value in db_session.identity_map.values()
    )


def test_report_planning_projects_excerpts_and_retains_only_selected_evidence(
    db_session, seed_users, monkeypatch
):
    user = seed_users["analyst"]
    user_id = user.id
    ids, _, filters, access = _seed_sources(
        db_session,
        user,
        count=6,
        body="Evidence details " * 60_000,
        summary="Source summary " * 30_000,
    )
    active = replace(load_active_ai_settings(db_session), report_max_sources=2)
    original = export_query._serialize_article

    def serialize(article, **kwargs):
        assert "text" in inspect(article).unloaded
        assert kwargs["text"] is None or len(kwargs["text"]) <= 2241
        return original(article, **kwargs)

    monkeypatch.setattr(export_query, "_serialize_article", serialize)
    plan = report_sources.build_report_source_plan(
        db_session,
        user_id=user_id,
        filters=filters,
        excluded_item_ids=[ids[-1]],
        prompt=ReportPromptConfig(),
        sections=[ReportSectionConfig(key="sources", title="Sources")],
        active=active,
        data_access=access,
    )
    assert plan.total_matches == 6
    assert len(plan.included_sources) == 2
    assert plan.metrics["articles_with_extracted_text"] == 2
    assert any("bounded excerpts" in warning for warning in plan.warnings)
    for source in plan.sources:
        assert source.record.summary is None
        assert source.record.article.text is None
        assert source.record.ai.summary is None
        assert source.record.article.text_available
        if source.included:
            assert (
                estimate_tokens(source.evidence_text) <= active.report_source_token_cap
            )
            assert source.citation_key in source.evidence_text
        else:
            assert source.evidence_text == ""
    assert {source.exclusion_reason for source in plan.sources} == {
        None,
        "excluded_by_user",
        "source_limit",
    }


def test_report_planning_total_payload_guard_is_actionable(
    db_session, seed_users, monkeypatch
):
    user = seed_users["analyst"]
    user_id = user.id
    _, _, filters, access = _seed_sources(db_session, user, count=3)
    monkeypatch.setattr(report_sources, "REPORT_SOURCE_PAYLOAD_MAX_BYTES", 2500)
    with pytest.raises(AIContextBudgetError, match="Exclude the source or narrow"):
        report_sources.build_report_source_plan(
            db_session,
            user_id=user_id,
            filters=filters,
            excluded_item_ids=[],
            prompt=ReportPromptConfig(),
            sections=[],
            active=load_active_ai_settings(db_session),
            data_access=access,
        )


@pytest.mark.parametrize(
    "export_format", ["csv", "jsonl", "threat_bundle", "stix", "misp", "pdf_bundle"]
)
def test_exports_can_omit_oversized_article_text(
    export_format, client, auth_headers, db_session, seed_users
):
    _, _, filters, _ = _seed_sources(
        db_session, seed_users["analyst"], body="x" * 9_000_000
    )
    response = client.post(
        "/exports",
        headers=auth_headers["analyst"],
        json={
            "format": export_format,
            "filters": filters.model_dump(mode="json"),
            "options": {
                "include_article_text": False,
                "csv_include_article_text": False,
                "pdf_include_article_text": False,
            },
        },
    )
    assert response.status_code == 200, response.text[:500]


def test_export_preview_and_download_explain_size_guard(
    client, auth_headers, db_session, seed_users
):
    _, _, filters, _ = _seed_sources(
        db_session, seed_users["analyst"], body="x" * 9_000_000
    )
    payload = {"filters": filters.model_dump(mode="json")}
    preview = client.post(
        "/exports/preview", headers=auth_headers["analyst"], json=payload
    )
    assert preview.status_code == 200
    assert preview.json()["items"][0]["has_article_text"] is True
    response = client.post(
        "/exports",
        headers=auth_headers["analyst"],
        json={
            **payload,
            "format": "jsonl",
            "options": {"include_article_text": True},
        },
    )
    assert response.status_code == 413
    assert "exclude article text" in response.json()["detail"]

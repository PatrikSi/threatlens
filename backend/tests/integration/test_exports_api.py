import csv
import io
import json
import threading
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from stix2 import parse

from app.api.routes import exports as exports_route
from app.models.article import Article
from app.models.audit_log import AuditLog
from app.models.data_policy import (
    DataPolicyState,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.iam import IAMPolicyState
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.models.item_state import ItemState
from app.models.tag import ItemTag, Tag
from app.models.user import User
from app.schemas.exports import ArticleExportRequest
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization
from app.services import export_query


@pytest.fixture(autouse=True)
def _disable_export_lock(monkeypatch: pytest.MonkeyPatch):
    @contextmanager
    def unlocked(**_kwargs):
        yield

    monkeypatch.setattr(exports_route, "acquire_export_lock", unlocked)


def test_export_capabilities_and_preview_filters(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    data = _seed_export_records(db_session, user_id=seed_users["viewer"].id)

    capabilities = client.get("/exports/capabilities", headers=auth_headers["viewer"])
    assert capabilities.status_code == 200
    capability_payload = capabilities.json()
    assert {entry["id"] for entry in capability_payload["formats"]} == {
        "csv",
        "jsonl",
        "threat_bundle",
        "stix",
        "misp",
        "pdf_bundle",
    }
    assert any(
        entry["id"] == str(data["matching_feed"].id)
        for entry in capability_payload["feeds"]
    )
    assert any(
        entry["id"] == str(data["tag"].id) for entry in capability_payload["tags"]
    )
    csv_capability = next(
        entry for entry in capability_payload["formats"] if entry["id"] == "csv"
    )
    assert csv_capability["supports_article_text"] is True

    preview = client.post(
        "/exports/preview",
        headers=auth_headers["viewer"],
        json={
            "filters": {
                "feed_ids": [str(data["matching_feed"].id)],
                "tag_ids": [str(data["tag"].id)],
                "classifications": ["vulnerability"],
                "ai_relevance_labels": ["high"],
                "ai_score_min": 0.8,
                "is_starred": True,
                "has_article_text": True,
                "since": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
            }
        },
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["total_matches"] == 1
    assert payload["articles_with_text"] == 1
    assert payload["items_with_iocs"] == 1
    assert payload["items"][0]["title"] == "=Critical vulnerability"
    assert payload["items"][0]["ioc_count"] == 2


@pytest.mark.parametrize(
    "export_format", ["csv", "jsonl", "threat_bundle", "stix", "misp", "pdf_bundle"]
)
def test_download_each_export_format(
    export_format: str,
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
):
    data = _seed_export_records(db_session, user_id=seed_users["analyst"].id)
    response = client.post(
        "/exports",
        headers=auth_headers["analyst"],
        json={
            "format": export_format,
            "filters": {"feed_ids": [str(data["matching_feed"].id)]},
            "options": {
                "include_article_text": True,
                "csv_include_article_text": True,
                "include_ai_details": True,
                "include_tag_metadata": True,
                "include_iocs": True,
                "include_ioc_csv": True,
                "include_user_state": True,
                "include_user_notes": True,
                "pdf_include_article_text": True,
                "stix_marking": "TLP:WHITE",
                "misp_distribution": 0,
                "filename_prefix": "Research export",
            },
        },
    )
    assert response.status_code == 200
    assert response.headers["x-export-item-count"] == "1"
    assert "research-export-" in response.headers["content-disposition"].lower()

    if export_format == "csv":
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert rows[0]["title"].startswith("'=Critical")
        assert rows[0]["note"] == "Review with IR"
        assert rows[0]["full_article_text"] == "Full extracted article text."
    elif export_format == "jsonl":
        payload = json.loads(response.text)
        assert payload["article"]["text"] == "Full extracted article text."
        assert payload["user_state"]["is_starred"] is True
    elif export_format == "stix":
        bundle = parse(response.text, allow_custom=False)
        assert any(entry.type == "report" for entry in bundle.objects)
    elif export_format == "misp":
        event = response.json()["response"][0]["Event"]
        assert event["published"] is False
        assert event["distribution"] == "0"
    else:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            if export_format == "threat_bundle":
                assert "articles.jsonl" in archive.namelist()
                assert "iocs.csv" in archive.namelist()
                rows = list(
                    csv.DictReader(
                        io.StringIO(archive.read("articles.csv").decode("utf-8-sig"))
                    )
                )
                assert rows[0]["full_article_text"] == "Full extracted article text."
            else:
                pdf_name = next(
                    name for name in archive.namelist() if name.endswith(".pdf")
                )
                assert archive.read(pdf_name).startswith(b"%PDF-")

    audit = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "exports.download")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.success is True
    assert audit.metadata_json["format"] == export_format
    assert isinstance(audit.metadata_json["iam_policy_revision"], int)
    assert isinstance(audit.metadata_json["data_policy_revision"], int)


def test_export_rejects_empty_selection(client: TestClient, auth_headers):
    response = client.post(
        "/exports",
        headers=auth_headers["viewer"],
        json={"format": "jsonl", "filters": {"q": "no-such-export-item"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "No articles match the selected filters."


def test_export_requires_authentication(client: TestClient):
    assert client.get("/exports/capabilities").status_code == 401
    assert client.post("/exports/preview", json={}).status_code == 401
    assert client.post("/exports", json={"format": "csv"}).status_code == 401


def test_export_preview_rejects_policy_revision_change(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch: pytest.MonkeyPatch,
):
    _seed_export_records(db_session, user_id=seed_users["viewer"].id)
    state = db_session.get(DataPolicyState, 1)
    assert state is not None
    calls = 0

    def changing_revision(_db):
        nonlocal calls
        calls += 1
        return state.revision if calls == 1 else state.revision + 1

    monkeypatch.setattr(
        export_query,
        "current_data_policy_revision",
        changing_revision,
    )

    response = client.post(
        "/exports/preview",
        headers=auth_headers["viewer"],
        json={},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "export_authorization_changed"
    assert "data access changed" in response.json()["detail"].lower()


def test_export_size_error_survives_audit_storage_failure(
    client: TestClient,
    auth_headers,
    db_session,
    seed_users,
    monkeypatch: pytest.MonkeyPatch,
):
    data = _seed_export_records(db_session, user_id=seed_users["viewer"].id)

    def fail_generation(*_args, **_kwargs):
        raise exports_route.ExportSizeLimitError("test size limit")

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(exports_route, "generate_export_artifact", fail_generation)
    monkeypatch.setattr(exports_route, "record_audit", fail_audit)

    response = client.post(
        "/exports",
        headers=auth_headers["viewer"],
        json={
            "format": "jsonl",
            "filters": {"feed_ids": [str(data["matching_feed"].id)]},
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert "configured size limit" in response.json()["detail"]


def test_download_holds_iam_and_data_policy_fences_through_file_stream(
    database_engine,
    request,
):
    run_id = uuid.uuid4()
    user_id = uuid.uuid4()
    feed_id = uuid.uuid4()
    item_id = uuid.uuid4()
    stream_started = threading.Event()
    release_stream = threading.Event()
    worker_done = threading.Event()
    worker_errors: list[BaseException] = []
    artifact_paths = []

    with Session(database_engine) as setup_db:
        user = User(
            id=user_id,
            email=f"export-stream-{run_id}@example.com",
            password_hash="not-used",
            role="viewer",
            is_active=True,
            is_approved=True,
        )
        feed = Feed(
            id=feed_id,
            name="Stream-fenced export",
            url=f"https://example.com/export-stream-{run_id}.xml",
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        )
        item = Item(
            id=item_id,
            feed_id=feed_id,
            source_guid=f"stream-{run_id}",
            url=f"https://example.com/export-stream-{run_id}",
            title="Stream-fenced item",
            dedupe_key=f"export-stream-{run_id}",
            content_hash="e" * 64,
            status="new",
        )
        setup_db.add_all([user, feed])
        setup_db.flush()
        setup_db.add(item)
        setup_db.commit()

    def cleanup() -> None:
        release_stream.set()
        worker.join(timeout=8)
        with Session(database_engine) as cleanup_db:
            cleanup_db.execute(
                delete(AuditLog).where(
                    AuditLog.actor_principal_type == "user",
                    AuditLog.actor_principal_id == user_id,
                )
            )
            cleanup_db.execute(delete(Item).where(Item.id == item_id))
            cleanup_db.execute(delete(Feed).where(Feed.id == feed_id))
            cleanup_db.execute(delete(User).where(User.id == user_id))
            cleanup_db.commit()

    request.addfinalizer(cleanup)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            stream_started.set()
            if not release_stream.wait(timeout=8):
                raise AssertionError("timed out waiting to finish the export stream")

    def stream_export() -> None:
        try:
            with Session(database_engine) as route_db:
                principal = route_db.get(User, user_id)
                assert principal is not None
                authorization = authorization_context_for_user(route_db, principal)
                data_access = data_access_context_for_authorization(
                    route_db, authorization
                )
                route_request = SimpleNamespace(
                    state=SimpleNamespace(authorization_context=authorization)
                )
                response = exports_route.download_export(
                    ArticleExportRequest.model_validate(
                        {
                            "format": "jsonl",
                            "filters": {"feed_ids": [str(feed_id)]},
                        }
                    ),
                    route_request,
                    route_db,
                    principal,
                    data_access,
                )
                artifact_paths.append(response.path)
                anyio.run(
                    response,
                    {
                        "type": "http",
                        "method": "POST",
                        "headers": [],
                    },
                    receive,
                    send,
                )
        except BaseException as exc:  # pragma: no cover - surfaced below
            worker_errors.append(exc)
        finally:
            worker_done.set()

    worker = threading.Thread(target=stream_export, daemon=True)
    worker.start()
    assert stream_started.wait(timeout=8)

    for state_model in (IAMPolicyState, DataPolicyState):
        with Session(database_engine) as writer_db:
            writer_db.execute(text("SET LOCAL lock_timeout = '250ms'"))
            with pytest.raises(OperationalError):
                writer_db.execute(
                    select(state_model).where(state_model.id == 1).with_for_update()
                ).scalar_one()
            writer_db.rollback()

    assert not worker_done.is_set()
    release_stream.set()
    worker.join(timeout=8)

    assert not worker.is_alive()
    assert not worker_errors
    assert artifact_paths
    assert not artifact_paths[0].exists()


def test_export_artifact_is_removed_when_streaming_disconnects(tmp_path):
    artifact_path = tmp_path / "interrupted.jsonl"
    artifact_path.write_bytes(b'{"sensitive": true}\n')
    response = exports_route.DisconnectSafeFileResponse(
        path=artifact_path,
        background=BackgroundTask(
            exports_route.remove_export_artifact,
            artifact_path,
        ),
    )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def disconnecting_send(message):
        if message["type"] == "http.response.body":
            raise RuntimeError("client disconnected")

    with pytest.raises(RuntimeError, match="client disconnected"):
        anyio.run(
            response,
            {"type": "http", "method": "POST", "headers": []},
            receive,
            disconnecting_send,
        )

    assert not artifact_path.exists()


def _seed_export_records(db_session, *, user_id: uuid.UUID):
    now = datetime.now(timezone.utc)
    matching_feed = Feed(
        name="Matching feed", url=f"https://example.com/export-{uuid.uuid4()}.xml"
    )
    other_feed = Feed(
        name="Other feed", url=f"https://example.net/export-{uuid.uuid4()}.xml"
    )
    db_session.add_all([matching_feed, other_feed])
    db_session.flush()

    matching_item = Item(
        feed_id=matching_feed.id,
        source_guid=f"matching-{uuid.uuid4()}",
        url="https://example.com/critical",
        title="=Critical vulnerability",
        summary="Source summary",
        published_at=now - timedelta(days=1),
        first_seen_at=now - timedelta(days=1),
        dedupe_key=f"export-matching-{uuid.uuid4()}",
        content_hash="a" * 64,
        status="content_fetched",
    )
    other_item = Item(
        feed_id=other_feed.id,
        source_guid=f"other-{uuid.uuid4()}",
        url="https://example.net/general",
        title="General security news",
        summary="Other summary",
        published_at=now - timedelta(days=30),
        first_seen_at=now - timedelta(days=30),
        dedupe_key=f"export-other-{uuid.uuid4()}",
        content_hash="b" * 64,
        status="new",
    )
    db_session.add_all([matching_item, other_item])
    db_session.flush()

    tag = Tag(name=f"critical-{uuid.uuid4().hex[:8]}")
    db_session.add(tag)
    db_session.flush()
    db_session.add(
        ItemTag(
            item_id=matching_item.id, tag_id=tag.id, source="manual", confidence=1.0
        )
    )
    db_session.add(
        ItemClassification(
            item_id=matching_item.id,
            primary_category="vulnerability",
            secondary_categories=["research"],
            confidence=0.9,
            scores_json={"vulnerability": 0.9},
            matched_terms_json={"vulnerability": ["title:critical"]},
            source_hash="c" * 64,
        )
    )
    db_session.add(
        ItemAIEnrichment(
            item_id=matching_item.id,
            status="completed",
            source_hash="d" * 64,
            summary_text="AI summary",
            relevance_score=0.95,
            relevance_label="high",
            relevance_reasons_json=["High operational relevance"],
            provider="test",
            model="test-model",
            generated_at=now,
        )
    )
    db_session.add(
        Article(
            item_id=matching_item.id,
            final_url=matching_item.url,
            retrieved_at=now,
            http_status=200,
            content_type="text/html",
            title_extracted=matching_item.title,
            text="Full extracted article text.",
            extraction_method="trafilatura",
            language="en",
            word_count=4,
        )
    )
    db_session.add(
        ItemState(
            user_id=user_id,
            item_id=matching_item.id,
            is_read=True,
            is_starred=True,
            note="Review with IR",
        )
    )
    ipv4 = IOC(type="ipv4", value_raw="203.0.113.9", value_norm="203.0.113.9")
    cve = IOC(type="cve", value_raw="CVE-2026-4321", value_norm="CVE-2026-4321")
    db_session.add_all([ipv4, cve])
    db_session.flush()
    db_session.add_all(
        [
            ItemIOC(
                item_id=matching_item.id,
                ioc_id=ipv4.id,
                source_section="article",
                occurrences=2,
                confidence=1.0,
            ),
            ItemIOC(
                item_id=matching_item.id,
                ioc_id=cve.id,
                source_section="title",
                occurrences=1,
                confidence=1.0,
            ),
        ]
    )
    db_session.commit()
    return {
        "matching_feed": matching_feed,
        "other_feed": other_feed,
        "matching_item": matching_item,
        "tag": tag,
    }

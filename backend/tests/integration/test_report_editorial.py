"""Publication is a current-authority transition over exact retained evidence."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.services.report_notifications import emit_report_ready_event
from app.services.report_publication import (
    ReportPublicationError,
    publish_automatic_report,
    validate_event_publication,
)
from app.services.report_queue import report_retry_queue, report_worker_queue


@pytest.fixture()
def draft(db_session, seed_users):
    now = datetime.now(timezone.utc)
    report = Report(
        owner_user_id=seed_users["admin"].id,
        title="Review me",
        status="ready",
        generation_stage="ready",
        period_start=now - timedelta(days=7),
        period_end=now,
        generated_at=now,
        summary_text="Summary [S1].",
        delivery_requested=True,
    )
    db_session.add(report)
    db_session.flush()
    db_session.add_all(
        [
            ReportSection(
                report_id=report.id,
                section_key="executive_summary",
                title="Summary",
                position=1,
                status="ready",
                body_markdown="Evidence claim [S1].",
                key_points_json=[],
                citations_json=["S1"],
            ),
            ReportSourceItem(
                report_id=report.id,
                citation_key="S1",
                included=True,
                rank=1,
                title_snapshot="Source",
                feed_name_snapshot="Feed",
                url_snapshot="https://example.com/article",
                first_seen_at_snapshot=now,
                evidence_text="Evidence claim.",
                tags_snapshot_json=[],
                iocs_snapshot_json=[],
                estimated_tokens=5,
            ),
        ]
    )
    db_session.commit()
    return report


def change(client, auth_headers, report, action, version, user="admin"):
    return client.post(
        f"/reports/{report.id}/editorial",
        headers=auth_headers[user],
        json={"action": action, "expected_version": version},
    )


def test_self_review_pins_revision_and_emits_only_after_publication(
    client, db_session, auth_headers, draft, monkeypatch
):
    monkeypatch.setattr(
        "app.tasks.integration_tasks.enqueue_integration_event_routing",
        lambda _ids: True,
    )
    with pytest.raises(ReportPublicationError, match="not been published"):
        emit_report_ready_event(db_session, report=draft)
    for version, action, expected in [
        (1, "submit", "review"),
        (2, "approve", "approved"),
        (3, "publish", "published"),
    ]:
        result = change(client, auth_headers, draft, action, version)
        assert result.status_code == 200, result.text
        assert result.json()["publication_status"] == expected
        assert result.json()["revision_current"] is True
    assert draft.approval_self_review is True
    assert (
        draft.review_revision_hash
        == draft.approved_revision_hash
        == draft.published_revision_hash
    )
    event = emit_report_ready_event(db_session, report=draft)
    assert event.schema_version == 3
    validate_event_publication(
        db_session, report_id=draft.id, payload=event.payload_json, schema_version=3
    )
    assert (
        len(
            list(
                db_session.scalars(
                    select(AuditLog).where(AuditLog.action.like("reports.editorial.%"))
                )
            )
        )
        == 3
    )
    assert change(client, auth_headers, draft, "publish", 3).status_code == 409
    assert change(client, auth_headers, draft, "return_to_draft", 4).status_code == 409


@pytest.mark.parametrize("field", ["body", "evidence"])
def test_changed_content_or_evidence_cannot_receive_old_approval(
    client, db_session, auth_headers, draft, field
):
    assert change(client, auth_headers, draft, "submit", 1).status_code == 200
    if field == "body":
        row = db_session.scalar(
            select(ReportSection).where(ReportSection.report_id == draft.id)
        )
        row.body_markdown = "Replacement claim [S1]."
    else:
        row = db_session.scalar(
            select(ReportSourceItem).where(ReportSourceItem.report_id == draft.id)
        )
        row.evidence_text = "Changed source."
    db_session.commit()
    rejected = change(client, auth_headers, draft, "approve", 2)
    assert rejected.status_code == 409, rejected.text
    assert "changed after submission" in rejected.text
    assert change(client, auth_headers, draft, "return_to_draft", 2).status_code == 200
    assert draft.approved_revision_hash is None
    assert draft.review_revision_hash is None


def test_published_evidence_mutation_blocks_delivery(db_session, draft):
    draft.review_required = False
    publish_automatic_report(db_session, draft)
    event = emit_report_ready_event(db_session, report=draft)
    db_session.scalar(
        select(ReportSourceItem).where(ReportSourceItem.report_id == draft.id)
    ).evidence_text = "Replaced."
    db_session.flush()
    with pytest.raises(ReportPublicationError, match="changed after publication"):
        validate_event_publication(
            db_session, report_id=draft.id, payload=event.payload_json, schema_version=3
        )


def test_publication_marker_cannot_be_omitted_for_modern_automatic_report(
    db_session, draft
):
    draft.review_required = False
    with pytest.raises(ReportPublicationError, match="missing"):
        validate_event_publication(
            db_session, report_id=draft.id, payload={}, schema_version=2
        )
    draft.editorial_contract_version = 0
    draft.review_required = False
    db_session.flush()
    validate_event_publication(
        db_session, report_id=draft.id, payload={}, schema_version=2
    )


def test_draft_revision_and_current_authority_are_required(
    client, db_session, auth_headers, draft, seed_users
):
    payload = {
        "expected_version": 1,
        "title": "Edited",
        "summary_text": "New summary.",
        "sections": [
            {
                "key": "executive_summary",
                "title": "Summary",
                "body_markdown": "Edited [S1].",
            }
        ],
    }
    denied = client.put(
        f"/reports/{draft.id}/draft", json=payload, headers=auth_headers["analyst"]
    )
    assert denied.status_code == 403
    saved = client.put(
        f"/reports/{draft.id}/draft", json=payload, headers=auth_headers["admin"]
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["editorial_version"] == 2
    assert saved.json()["coverage"]["grounding"]["status"] == "human_edited"
    assert (
        client.put(
            f"/reports/{draft.id}/draft", json=payload, headers=auth_headers["admin"]
        ).status_code
        == 409
    )
    assert change(client, auth_headers, draft, "submit", 2).status_code == 200
    seed_users["analyst"].is_active = False
    db_session.commit()
    assert (
        change(client, auth_headers, draft, "approve", 3, user="analyst").status_code
        == 403
    )


def test_unknown_citations_and_unsafe_text_are_rejected(
    client, db_session, auth_headers, draft
):
    section = db_session.scalar(
        select(ReportSection).where(ReportSection.report_id == draft.id)
    )
    section.body_markdown = "Unknown source [S9]."
    db_session.commit()
    assert change(client, auth_headers, draft, "submit", 1).status_code == 409
    payload = {
        "expected_version": 1,
        "title": "Unsafe\x00",
        "summary_text": "",
        "sections": [
            {"key": "executive_summary", "title": "Summary", "body_markdown": "Safe"}
        ],
    }
    assert (
        client.put(
            f"/reports/{draft.id}/draft", json=payload, headers=auth_headers["admin"]
        ).status_code
        == 422
    )


def test_editorial_queue_preserves_legacy_retries(db_session, draft):
    assert report_worker_queue(draft) == "ai-reports-v3"
    draft.editorial_contract_version = 0
    assert report_worker_queue(draft) == "ai-reports-v2"
    task = SimpleNamespace(
        request=SimpleNamespace(delivery_info={"routing_key": "ai-reports-v2"})
    )
    assert report_retry_queue(task) == "ai-reports-v2"


@pytest.mark.parametrize("review_required", [True, False])
def test_generation_finalizer_obeys_review_policy(db_session, draft, review_required):
    from app.models.integration import IntegrationEvent
    from app.services.report_generation import _UsageCounters, _finalize_ready_report

    draft.status = "running"
    draft.review_required = review_required
    _finalize_ready_report(db_session, report=draft, counters=_UsageCounters())
    db_session.flush()
    events = list(
        db_session.scalars(
            select(IntegrationEvent).where(IntegrationEvent.source_id == str(draft.id))
        )
    )
    assert draft.status == "ready"
    assert draft.publication_status == ("draft" if review_required else "published")
    assert len(events) == (0 if review_required else 1)


def test_approval_does_not_survive_return_to_draft(
    client, db_session, auth_headers, draft
):
    assert change(client, auth_headers, draft, "submit", 1).status_code == 200
    assert change(client, auth_headers, draft, "approve", 2).status_code == 200
    assert change(client, auth_headers, draft, "return_to_draft", 3).status_code == 200
    assert draft.approved_revision_hash is None
    assert draft.approved_at is None
    assert change(client, auth_headers, draft, "publish", 4).status_code == 409


def test_expiration_during_response_materialization_rolls_back_transition(
    client, db_session, auth_headers, draft, monkeypatch
):
    from app.api.routes import report_editorial as routes
    from app.models.api_token import ApiToken

    original = routes.report_detail_response

    def expire_after_read(db, *, report):
        response = original(db, report=report)
        token = db.scalar(
            select(ApiToken).where(ApiToken.user_id == draft.owner_user_id)
        )
        token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.flush()
        return response

    monkeypatch.setattr(routes, "report_detail_response", expire_after_read)
    response = change(client, auth_headers, draft, "submit", 1)
    assert response.status_code == 403, response.text
    db_session.refresh(draft)
    assert draft.publication_status == "draft"
    assert draft.editorial_version == 1


def test_library_filters_publication_separately_from_generation(
    client, auth_headers, draft
):
    visible = client.get(
        "/reports/library?status=ready&publication_status=draft",
        headers=auth_headers["admin"],
    )
    assert visible.status_code == 200
    assert [entry["id"] for entry in visible.json()["items"]] == [str(draft.id)]
    hidden = client.get(
        "/reports/library?status=ready&publication_status=published",
        headers=auth_headers["admin"],
    )
    assert hidden.status_code == 200
    assert hidden.json()["items"] == []

"""Only current successful, canonically exact AI evidence affects automation."""

from sqlalchemy import select

from app.models.integration import IntegrationEvent
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.services.ai_integration import run_item_ai_enrichment
from app.services.indicator_evidence import current_ai_indicator_links
from app.services.indicator_evidence import current_ai_attack_techniques
from app.services.webhook_conditions import event_condition_values, evaluate_conditions
from app.schemas.webhook_automation import WebhookConditionGroup
from app.services.intel_events import emit_intel_events
from app.services.ioc_extraction import extract_iocs
from app.services.ioc_storage import replace_item_iocs
from tests.unit.test_ai_extraction_workflow import (
    completion,
    extraction_item as extraction_item,
)


def test_ai_completion_emits_current_role_change_once_and_failed_refresh_stops_reuse(
    db_session,
    extraction_item,
    monkeypatch,
):
    item, article = extraction_item
    replace_item_iocs(
        db_session,
        item_id=item.id,
        extracted=extract_iocs(
            title=item.title, summary=item.summary, article_text=article.text
        ),
    )
    emit_intel_events(db_session, item_id=item.id, deterministic=True)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json", lambda *_a, **_kw: completion()
    )
    first = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert first.status == "ready"
    links = current_ai_indicator_links(db_session, item.id)
    assert links[("domain", "docs.example.org")]["role"] == "reference"
    assert links[("domain", "docs.example.org")]["maliciousness_confidence"] is None
    events = db_session.scalars(
        select(IntegrationEvent)
        .where(IntegrationEvent.source_id == str(item.id))
        .order_by(IntegrationEvent.created_at)
    ).all()
    assert [row.event_type for row in events].count("intel.indicators.changed") == 2
    latest = events[-1].payload_json
    reference = next(
        row for row in latest["indicators"] if row["value"] == "docs.example.org"
    )
    assert reference["role"] == "reference" and reference["ai_evidence"]
    assert emit_intel_events(db_session, item_id=item.id, deterministic=False) == []
    first.enrichment.status = "error"
    db_session.flush()
    assert current_ai_indicator_links(db_session, item.id) == {}


def test_stale_structured_result_cannot_attach_to_new_success_provenance(
    db_session,
    extraction_item,
    monkeypatch,
):
    item, _article = extraction_item
    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json", lambda *_a, **_kw: completion()
    )
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert current_ai_indicator_links(db_session, item.id)
    enrichment = db_session.get(ItemAIEnrichment, item.id)
    old = dict(enrichment.structured_extraction_json)
    old["source_hash"] = "0" * 64
    enrichment.structured_extraction_json = old
    db_session.flush()
    assert current_ai_indicator_links(db_session, item.id) == {}
    assert result.status == "ready"


def test_exact_match_does_not_link_a_domain_from_a_url_entity(
    db_session,
    extraction_item,
    monkeypatch,
):
    item, _article = extraction_item
    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json", lambda *_a, **_kw: completion()
    )
    run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    enrichment = db_session.get(ItemAIEnrichment, item.id)
    payload = dict(enrichment.structured_extraction_json)
    payload["entities"] = [
        {**payload["entities"][2], "name": "https://docs.example.org/path"}
    ]
    payload["relationships"] = []
    enrichment.structured_extraction_json = payload
    db_session.flush()
    links = current_ai_indicator_links(db_session, item.id)
    assert ("domain", "docs.example.org") not in links
    assert ("url", "https://docs.example.org/path") in links


def test_attack_conditions_use_current_verified_passages_not_inferred_descriptions(
    db_session,
    extraction_item,
    monkeypatch,
):
    from tests.unit.test_ai_extraction import extraction_payload

    item, article = extraction_item
    quote = "The report identifies PowerShell behavior as ATT&CK T1059.001."
    article.text += "\n" + quote
    payload = extraction_payload()
    payload["entities"].append(
        {
            "id": "e5",
            "kind": "behavior",
            "name": "PowerShell",
            "description": "The model also speculates about T9999.",
            "assertion": "inferred",
            "evidence": [{"source": "article_text", "quote": quote}],
            "versions": [],
            "indicator_role": None,
        }
    )
    replace_item_iocs(
        db_session,
        item_id=item.id,
        extracted=extract_iocs(
            title=item.title, summary=item.summary, article_text=article.text
        ),
    )
    emit_intel_events(db_session, item_id=item.id, deterministic=True)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.ai_integration._call_ai_json",
        lambda *_a, **_kw: completion(payload),
    )
    result = run_item_ai_enrichment(db_session, item_id=item.id, force=True)
    db_session.commit()
    assert result.status == "ready", result.enrichment.error
    assert current_ai_attack_techniques(db_session, item.id) == ["T1059.001"]
    event = db_session.scalar(
        select(IntegrationEvent)
        .where(IntegrationEvent.source_id == str(item.id))
        .order_by(IntegrationEvent.created_at.desc())
        .limit(1)
    )
    condition = WebhookConditionGroup.model_validate(
        {
            "op": "all",
            "conditions": [
                {"field": "attack_technique", "operator": "in", "value": ["T1059.001"]}
            ],
        }
    )
    assert evaluate_conditions(
        condition,
        event_condition_values(
            event.payload_json, created_at=event.created_at, event_type=event.event_type
        ),
    )[0]
    assert event.payload_json["filter_metadata"]["attack_techniques"] == ["T1059.001"]
    enrichment = result.enrichment
    enrichment.status = "error"
    db_session.flush()
    assert current_ai_attack_techniques(db_session, item.id) == []
    enrichment.status = "ready"
    enrichment.structured_extraction_json = {
        **enrichment.structured_extraction_json,
        "source_hash": "0" * 64,
    }
    db_session.flush()
    assert current_ai_attack_techniques(db_session, item.id) == []

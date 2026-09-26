from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from types import SimpleNamespace
import uuid

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.notification import NotificationWebhookWrite
from app.schemas.webhook_automation import WebhookConditionGroup, WebhookCredentialWrite
from app.services.webhook_conditions import evaluate_conditions, event_condition_values
from app.services.webhook_credentials import signed_headers
from app.services.webhook_automation import automation_envelope


def _condition(**kwargs):
    return WebhookConditionGroup.model_validate({"op": "all", "conditions": [kwargs]})


@pytest.mark.parametrize("op", ["all", "any", "not"])
def test_missing_fields_never_satisfy_negative_conditions(op):
    condition = WebhookConditionGroup.model_validate(
        {
            "op": op,
            "conditions": [
                {"field": "ioc_role", "operator": "not_in", "value": ["reference"]}
            ],
        }
    )
    matched, checks, missing = evaluate_conditions(condition, {})
    assert matched is False and missing == ["ioc_role"]
    assert checks[0].matched is False


def test_independent_confidences_and_incomplete_tag_metadata_fail_closed():
    now = datetime.now(timezone.utc)
    values = event_condition_values(
        {
            "indicators": [
                {
                    "type": "domain",
                    "role": "malicious",
                    "extraction_confidence": 0.99,
                    "maliciousness_confidence": None,
                }
            ],
            "filter_metadata": {"tags": ["endpoint"], "tags_complete": False},
        },
        created_at=now - timedelta(seconds=10),
        now=now,
    )
    assert values["freshness_seconds"] == 10
    assert evaluate_conditions(
        _condition(field="extraction_confidence", operator="gte", value=0.95), values
    )[0]
    assert not evaluate_conditions(
        _condition(field="maliciousness_confidence", operator="gte", value=0.5), values
    )[0]
    assert not evaluate_conditions(
        _condition(field="tag", operator="not_in", value=["cloud"]), values
    )[0]


@pytest.mark.parametrize(
    "field,operator,value",
    [
        ("tag", "gte", 0.5),
        ("extraction_confidence", "in", ["0.9"]),
        ("extraction_confidence", "gte", 1.5),
        ("tag", "in", []),
        ("ioc_role", "in", ["x" * 201]),
    ],
)
def test_condition_type_and_size_boundaries(field, operator, value):
    with pytest.raises(ValidationError):
        _condition(field=field, operator=operator, value=value)


def test_nested_condition_budget():
    node = {"field": "tag", "operator": "in", "value": ["endpoint"]}
    for _ in range(4):
        node = {"op": "not", "conditions": [node]}
    with pytest.raises(ValidationError):
        WebhookConditionGroup.model_validate(node)


@pytest.mark.parametrize(
    "event_type",
    ["intel.extraction.ready", "intel.indicators.changed", "hunt.approved"],
)
def test_new_event_does_not_make_old_evidence_fresh(event_type):
    now = datetime.now(timezone.utc)
    values = event_condition_values(
        {"article_retrieved_at": (now - timedelta(days=20)).isoformat()},
        created_at=now,
        now=now,
        event_type=event_type,
    )
    assert not evaluate_conditions(
        _condition(field="freshness_seconds", operator="lte", value=3600), values
    )[0]
    missing = event_condition_values({}, created_at=now, now=now, event_type=event_type)
    assert missing["freshness_seconds"] is None


def test_confidence_compares_the_minimum_inventory_score():
    now = datetime.now(timezone.utc)
    values = event_condition_values(
        {
            "indicators": [
                {"extraction_confidence": 0.2},
                {"extraction_confidence": 0.99},
            ]
        },
        created_at=now,
    )
    assert not evaluate_conditions(
        _condition(field="extraction_confidence", operator="gte", value=0.9), values
    )[0]
    assert evaluate_conditions(
        _condition(field="extraction_confidence", operator="lte", value=0.5), values
    )[0]


def test_signature_covers_exact_http_body_and_attempt_identity():
    request = httpx.Request(
        "POST",
        "https://siem.example/hunts",
        json={"indicators": [{"value": "é.example"}], "score": 0.8},
    )
    headers = signed_headers(
        secret="s" * 32,
        body=request.content,
        event_id="logical-event",
        attempt_id="attempt-1",
        timestamp=123,
    )
    expected = hmac.new(
        b"s" * 32,
        b"v1\n123\nlogical-event\nattempt-1\n" + request.content,
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-ThreatLens-Signature"] == f"v1={expected}"
    assert (
        signed_headers(
            secret="s" * 32,
            body=request.content,
            event_id="logical-event",
            attempt_id="attempt-2",
            timestamp=123,
        )["X-ThreatLens-Signature"]
        != headers["X-ThreatLens-Signature"]
    )


def test_automation_envelope_preserves_types_and_stable_action():
    event = SimpleNamespace(
        id=uuid.uuid4(),
        event_type="intel.extraction.ready",
        created_at=datetime.now(timezone.utc),
        source_type="item",
        source_id=uuid.uuid4(),
        payload_json={
            "action_id": "revision-one",
            "indicators_complete": True,
            "indicators": [{"confidence": 0.9}],
            "revision": 7,
        },
    )
    result = json.loads(json.dumps(automation_envelope(event)))
    assert result["data"]["indicators"][0]["confidence"] == 0.9
    assert result["data"]["indicators_complete"] is True
    assert result["action_id"] == "revision-one"


def test_legacy_defaults_and_native_event_mode_contract():
    legacy = NotificationWebhookWrite(
        name="Old client", url_template="https://hooks.example/send"
    )
    assert legacy.payload_mode == "template" and legacy.conditions is None
    with pytest.raises(ValidationError):
        NotificationWebhookWrite(
            name="New event",
            url_template="https://hooks.example/send",
            event_type="hunt.approved",
        )
    with pytest.raises(ValidationError):
        WebhookCredentialWrite(
            name="Bad header", auth_type="header", header_name="X-ThreatLens-Signature"
        )


@pytest.mark.parametrize(
    "event_type",
    [
        "rss_item_new",
        "alert_match",
        "feed_failing",
        "webhook_failed",
        "daily_digest",
        "report_ready",
    ],
)
def test_automation_marker_preserves_each_legacy_event_after_mode_change(event_type):
    from types import SimpleNamespace
    from app.services.webhook_automation import preserve_saved_automation_request

    generic = SimpleNamespace(
        payload_json={"webhook_payload_mode_snapshot": "automation_v1"}
    )
    db = SimpleNamespace(get=lambda *_: generic)
    delivery = SimpleNamespace(
        event_type_snapshot=event_type,
        integration_delivery_id="retained",
        rendered_body="{}",
    )
    assert preserve_saved_automation_request(
        db, webhook=SimpleNamespace(payload_mode="template"), delivery=delivery
    )


def test_markerless_deep_template_json_does_not_break_retry_fallback():
    from types import SimpleNamespace
    from app.services.webhook_automation import preserve_saved_automation_request

    delivery = SimpleNamespace(
        event_type_snapshot="rss_item_new",
        integration_delivery_id=None,
        rendered_body="[" * 1500 + "]" * 1500,
    )
    assert not preserve_saved_automation_request(
        None, webhook=SimpleNamespace(payload_mode="template"), delivery=delivery
    )


@pytest.mark.parametrize("value", ["tag\x00value", "tag\ud800value"])
def test_condition_text_is_storage_safe_before_persistence(value):
    with pytest.raises(ValidationError, match="cannot be stored"):
        _condition(field="tag", operator="in", value=[value])


@pytest.mark.parametrize(
    "field,operator,value",
    [
        ("tag", "not_in", [None]),
        ("tag", "in", {"name": "endpoint"}),
        ("extraction_confidence", "gte", "high"),
        ("extraction_confidence", "gte", float("nan")),
        ("maliciousness_confidence", "lte", -1),
        ("maliciousness_confidence", "lte", 2),
        ("maliciousness_confidence", "lte", 10**500),
    ],
)
def test_invalid_event_fields_stay_unknown_under_negation(field, operator, value):
    condition = WebhookConditionGroup.model_validate(
        {
            "op": "not",
            "conditions": [
                {
                    "field": field,
                    "operator": operator,
                    "value": ["endpoint"] if field == "tag" else 0.5,
                }
            ],
        }
    )
    matched, _checks, missing = evaluate_conditions(condition, {field: value})
    assert not matched and missing == [field]


def test_malformed_retained_metadata_does_not_break_preview():
    values = event_condition_values(
        {"filter_metadata": ["tags"], "tags": 42, "hunt": ["accepted"]},
        created_at=datetime.now(timezone.utc),
    )
    assert values["tag"] == []
    assert values["hunt_review_status"] is None

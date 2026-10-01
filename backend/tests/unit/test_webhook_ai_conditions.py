from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.webhook_automation import WebhookConditionGroup
from app.services.webhook_conditions import evaluate_conditions, event_condition_values


def _condition(field="ai_relevance_score", operator="gte", value=0.8, op="all"):
    return WebhookConditionGroup.model_validate({
        "op": op, "conditions": [{"field": field, "operator": operator, "value": value}],
    })


def _payload():
    return {
        "source_revision": 3, "article_id": "article",
        "ai_relevance": {
            "verified": True, "score": 0.9, "label": "high", "source_hash": "hash",
            "provenance": {"version": 1, "source_hash": "hash", "source_version": 3,
                           "article_id": "article"},
        },
    }


def _values(payload):
    return event_condition_values(payload, created_at=datetime.now(timezone.utc))


def test_shared_relevance_has_independent_score_and_label_conditions():
    values = _values(_payload())
    assert evaluate_conditions(_condition(), values)[0]
    assert evaluate_conditions(_condition("ai_relevance_label", "in", ["high"]), values)[0]
    assert not evaluate_conditions(_condition(value=0.95), values)[0]
    assert not evaluate_conditions(_condition("ai_relevance_label", "not_in", ["high"]), values)[0]


@pytest.mark.parametrize("change", ["absent", "unverified", "source", "article", "hash"])
@pytest.mark.parametrize("op", ["all", "any", "not"])
def test_unverified_or_mismatched_relevance_stays_unknown_under_negation(change, op):
    payload = _payload()
    if change == "absent":
        payload.pop("ai_relevance")
    elif change == "unverified":
        payload["ai_relevance"]["verified"] = False
    elif change == "source":
        payload["source_revision"] += 1
    elif change == "article":
        payload["article_id"] = "new-article"
    else:
        payload["ai_relevance"]["source_hash"] = "new-hash"
    payload["filter_metadata"] = {"ai_relevance_label": "high", "ai_relevance_score": 1}
    matched, _checks, missing = evaluate_conditions(
        _condition("ai_relevance_label", "not_in", ["low"], op=op), _values(payload),
    )
    assert not matched
    assert missing == ["ai_relevance_label"]


@pytest.mark.parametrize("score", [True, -0.1, 1.1, float("nan"), float("inf"), "0.9", {}, []])
def test_malformed_snapshot_scores_are_unavailable(score):
    payload = _payload()
    payload["ai_relevance"]["score"] = score
    assert evaluate_conditions(_condition(op="not"), _values(payload))[2] == ["ai_relevance_score"]


@pytest.mark.parametrize("field,operator,value", [
    ("ai_relevance_score", "gte", 1.1),
    ("ai_relevance_score", "in", ["0.5"]),
    ("ai_relevance_label", "gte", 0.5),
    ("ai_relevance_label", "in", ["critical"]),
])
def test_ai_condition_contract_rejects_unsupported_values(field, operator, value):
    with pytest.raises(ValidationError):
        _condition(field, operator, value)


def test_shared_relevance_cannot_be_used_as_same_indicator_evidence():
    with pytest.raises(ValidationError, match="only indicator fields"):
        _condition(op="indicators_any")

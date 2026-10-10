from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.webhook_automation import WebhookConditionGroup
from app.services.webhook_conditions import evaluate_conditions, event_condition_values


def predicate(op="indicators_any", *, conditions=None):
    return WebhookConditionGroup.model_validate({
        "op": op, "conditions": conditions or [
            {"field": "ioc_type", "operator": "in", "value": ["domain"]},
            {"field": "ioc_role", "operator": "in", "value": ["malicious_infrastructure"]},
        ],
    })


def indicator(identifier, kind="domain", role="malicious_infrastructure", **changes):
    return {"id": identifier, "type": kind, "value": f"{identifier}.invalid",
            "role": role, "excluded": False, **changes}


def values(entries, **changes):
    return event_condition_values(
        {"indicators": entries, "indicators_complete": True, **changes},
        created_at=datetime.now(timezone.utc),
    )


def test_same_indicator_does_not_cross_match_independent_values():
    snapshot = values([
        indicator("reference", role="reference"),
        indicator("malicious", kind="ipv4"),
    ])
    assert evaluate_conditions(predicate("all"), snapshot)[0] is True
    matched, checks, missing = evaluate_conditions(predicate(), snapshot)
    assert matched is False and missing == []
    assert [row.indicator_id for row in checks if row.indicator_id] == ["reference", "malicious"]
    assert all(not row.matched for row in checks)


@pytest.mark.parametrize("op,expected", [("indicators_any", True), ("indicators_all", False)])
def test_quantifiers_apply_whole_predicate_to_each_indicator(op, expected):
    assert evaluate_conditions(predicate(op), values([
        indicator("one"), indicator("two", role="reference"),
    ]))[0] is expected


@pytest.mark.parametrize("op", ["indicators_any", "indicators_all"])
def test_excluded_indicators_are_visible_but_never_eligible(op):
    matched, checks, missing = evaluate_conditions(predicate(op), values([
        indicator("blocked", excluded=True),
    ]))
    assert not matched and not missing
    assert checks[0].indicator_excluded is True
    assert checks[0].reason == "Excluded indicator is not eligible"
    assert not evaluate_conditions(predicate(op), values([]))[0]


def test_current_verdict_and_confidence_can_match_the_same_indicator():
    condition = predicate(conditions=[
        {"field": "analyst_verdict", "operator": "in", "value": ["malicious"]},
        {"field": "extraction_confidence", "operator": "gte", "value": 0.9},
    ])
    assert not evaluate_conditions(condition, values([
        indicator("one", analyst_verdict="malicious", extraction_confidence=0.8),
        indicator("two", analyst_verdict="reference", extraction_confidence=0.99),
    ]))[0]
    assert evaluate_conditions(condition, values([
        indicator("one", analyst_verdict="MALICIOUS", extraction_confidence=0.95),
    ]))[0]


@pytest.mark.parametrize("snapshot", [
    None, "invalid", [None], [indicator("one", excluded="false")],
    [dict(type="domain", role="malicious_infrastructure")],
    [indicator(str(index)) for index in range(251)],
])
def test_malformed_inventory_remains_unknown_under_negation(snapshot):
    condition = WebhookConditionGroup(op="not", conditions=[predicate()])
    matched, _checks, missing = evaluate_conditions(condition, values(snapshot))
    assert not matched and missing == ["indicators"]


def test_incomplete_inventory_cannot_be_repaired_by_filter_metadata():
    snapshot = values([indicator("one")], indicators_complete=False,
                      filter_metadata={"_indicators": [indicator("injected")]})
    matched, _checks, missing = evaluate_conditions(predicate(), snapshot)
    assert not matched and missing == ["indicators"]


@pytest.mark.parametrize("score", [None, True, -1, 2, float("nan"), "0.99"])
def test_unknown_confidence_cannot_match_through_not(score):
    condition = predicate(conditions=[{
        "op": "not", "conditions": [
            {"field": "maliciousness_confidence", "operator": "lte", "value": 0.5},
        ],
    }])
    matched, _checks, missing = evaluate_conditions(condition, values([
        indicator("one", maliciousness_confidence=score),
    ]))
    assert not matched and missing == ["maliciousness_confidence"]


def test_disjunction_can_match_known_indicator_even_with_unknown_peer():
    condition = predicate(conditions=[
        {"field": "maliciousness_confidence", "operator": "gte", "value": 0.8},
    ])
    assert evaluate_conditions(condition, values([
        indicator("unknown"), indicator("known", maliciousness_confidence=0.9),
    ]))[0]


def test_indicator_scope_rejects_event_fields_and_nested_quantifiers():
    with pytest.raises(ValidationError, match="only indicator fields"):
        predicate(conditions=[{"field": "team_id", "operator": "in", "value": ["team"]}])
    with pytest.raises(ValidationError, match="cannot be nested"):
        predicate(conditions=[predicate().model_dump()])


def test_preview_is_bounded_without_truncating_evaluation():
    condition = WebhookConditionGroup(op="all", conditions=[predicate(), predicate()])
    matched, checks, _missing = evaluate_conditions(condition, values([
        indicator(str(index)) for index in range(250)
    ]))
    assert matched
    assert len([entry for entry in checks if entry.indicator_id]) == 250
    assert len(checks) == 252
    assert "250 preview rows omitted" in checks[-1].reason

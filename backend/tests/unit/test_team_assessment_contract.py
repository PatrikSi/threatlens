import copy
import json

import pytest

from app.services.team_assessment_contract import validate_team_assessment_output


def _messages(*, hunts_enabled=True):
    return [{"role": "user", "content": json.dumps({
        "task": "team_assessment", "hunts_enabled": hunts_enabled,
        "team_context": {"available_telemetry": ["Endpoint process creation"]},
        "item": {"title": "A reported intrusion", "summary": "", "article_text": "The actor used PowerShell to execute downloaded scripts."},
    })}]


def _result():
    return {
        "relevance_score": 0.7,
        "relevance_reasons": ["The team maintains Windows endpoints."],
        "information_gaps": ["No local evidence of this activity was supplied."],
        "hunts": [{
            "title": "Review unusual PowerShell process activity",
            "hypothesis": "Similar behavior may be visible in endpoint process telemetry.",
            "rationale": "The report describes PowerShell and this team has endpoint process logs.",
            "required_logs": ["Endpoint process creation"],
            "benign_explanations": ["Authorized administration scripts"],
            "information_gaps": ["No local prevalence baseline was supplied."],
            "evidence": [{"source": "article_text", "quote": "The actor used PowerShell to execute downloaded scripts."}],
            "attack_technique_ids": ["T1059.001"],
            "detection_strategy_ids": [],
        }],
    }


def test_team_hypotheses_preserve_reported_evidence_and_accept_empty_mapping():
    result = _result()
    assert validate_team_assessment_output(result, _messages()) == result
    result["hunts"][0]["attack_technique_ids"] = []
    assert validate_team_assessment_output(result, _messages())["hunts"][0]["attack_technique_ids"] == []


@pytest.mark.parametrize("mutation", [
    lambda r: r["hunts"][0]["evidence"][0].update(quote="This supporting passage was invented."),
    lambda r: r["hunts"][0]["evidence"][0].update(source="team_context"),
    lambda r: r["hunts"][0].update(attack_technique_ids=["T9999"]),
    lambda r: r["hunts"][0].update(detection_strategy_ids=["DET9999"]),
    lambda r: r["hunts"][0].update(attack_technique_ids=["T1059.001", "T1059.001"]),
    lambda r: r["hunts"][0].update(review_status="accepted"),
    lambda r: r["hunts"][0].update(investigation_id="external-override"),
    lambda r: r["hunts"][0].update(required_logs=[]),
    lambda r: r["hunts"][0].update(benign_explanations=[]),
    lambda r: r["hunts"][0].update(evidence=[]),
    lambda r: r.update(relevance_score=float("nan")),
    lambda r: r.update(relevance_score=float("inf")),
    lambda r: r.update(relevance_score=70),
    lambda r: r.update(hunts=[copy.deepcopy(r["hunts"][0]) for _ in range(4)]),
    lambda r: r.update(relevance_reasons=["x" * 401]),
])
def test_invalid_or_unbounded_team_output_is_rejected(mutation):
    result = _result()
    mutation(result)
    with pytest.raises(ValueError):
        validate_team_assessment_output(result, _messages())


def test_hunt_toggle_is_enforced_in_provider_output_not_only_the_prompt():
    result = _result()
    with pytest.raises(ValueError, match="disabled"):
        validate_team_assessment_output(result, _messages(hunts_enabled=False))
    result["hunts"] = []
    assert validate_team_assessment_output(result, _messages(hunts_enabled=False)) == result


def test_team_profile_cannot_be_used_as_article_evidence():
    result = _result()
    result["hunts"][0]["evidence"] = [{"source": "article_text", "quote": "Endpoint process creation"}]
    with pytest.raises(ValueError, match="exactly match"):
        validate_team_assessment_output(result, _messages())


def test_missing_prompt_provenance_fails_closed():
    with pytest.raises(ValueError, match="source input"):
        validate_team_assessment_output(_result(), None)


def test_generic_provider_entry_point_cannot_omit_team_authorization():
    from app.services.ai_integration import request_ai_json_with_usage
    from app.services.ai_provider_client import AIIntegrationError

    # Refuse before accessing even a database or active provider configuration.
    with pytest.raises(AIIntegrationError, match="authorization fence"):
        request_ai_json_with_usage(None, None, feature_type="team_assessment", messages=_messages())

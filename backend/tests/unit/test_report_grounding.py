import json

import pytest

from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_output_validation import validate_feature_completion
from app.services.report_grounding import ReportGroundingError, validate_findings, validate_section
from types import SimpleNamespace


SOURCES = {"S1": "[S1] Advisory\nThe vendor confirmed active exploitation.\nPatching is recommended."}


def finding():
    return {"text": "Active exploitation was confirmed.", "citations": ["S1"],
            "evidence_quotes": [{"citation": "S1", "quote": "The vendor confirmed active exploitation."}]}


def section(body="Active exploitation was confirmed. [S1]", **kwargs):
    return {"body_markdown": body, "citations": ["S1"], **kwargs}


def test_findings_require_actual_quotes_and_batch_local_citations():
    assert validate_findings([finding()], sources=SOURCES) == [finding()]
    assert validate_findings([], sources=SOURCES) == []
    value = finding()
    value["evidence_quotes"][0]["quote"] = "active exploitation. Patching is recommended."
    assert validate_findings([value], sources=SOURCES)
    for malformed in ["source title", {}, {**finding(), "evidence_quotes": []},
                      {**finding(), "citations": ["S2"]}, {**finding(), "text": "Unsupported [S2]"},
                      {**finding(), "evidence_quotes": [{"citation": "S1", "quote": "The vendor reported no exploitation."}]}]:
        with pytest.raises(ReportGroundingError):
            validate_findings([malformed], sources=SOURCES)


@pytest.mark.parametrize("body", [
    "Uncited claim.\n\nCited claim. [S1]",
    "Claim with a forged citation. [S2]",
    "Claim with an inline code marker. `[S1]`",
    "[Claim with a link label [S1]](https://example.test)",
    "[Uncited linked claim](https://example.test)\n\nCited claim. [S1]",
    "- Cited finding [S1]\n- Uncited finding",
    "| Finding | Source |\n| --- | --- |\n| Uncited claim | none |\n| Cited claim | [S1] |",
    "# Heading [S1]\n\n```\n[S1]\n```",
])
def test_claim_blocks_cannot_borrow_citations_from_another_block_or_code(body):
    with pytest.raises(ReportGroundingError):
        validate_section(section(body), known_citations={"S1"})


def test_sections_check_lists_table_rows_and_key_points():
    result = validate_section(section(
        "## Findings\n\n**Active exploitation** was confirmed. [S1]\n\n"
        "- Patch promptly. [S1]\n  - Review exposed assets. [S1]\n\n"
        "| Finding | Priority | Source |\n| --- | --- | --- |\n| Exploitation | High | [S1] |",
        key_points=["Review exposure. [S1]"],
    ), known_citations={"S1", "S2"})
    assert result.claim_blocks == 4
    assert result.citations == ["S1"]
    for invalid in [section(citations=["S1", "S2"]), section(key_points=["Unsupported recommendation."])]:
        with pytest.raises(ReportGroundingError):
            validate_section(invalid, known_citations={"S1", "S2"})


def test_received_invalid_output_keeps_usage_and_is_retryable_before_settlement():
    completion = AICompletionResult(payload={"findings": [{**finding(), "evidence_quotes": []}]},
        provider="openai_compatible", model="model", latency_ms=42,
        prompt_tokens=10, completion_tokens=20, total_tokens=30)
    messages = [{"role": "user", "content": json.dumps({"evidence": list(SOURCES.values())})}]
    with pytest.raises(AIIntegrationError) as caught:
        validate_feature_completion(SimpleNamespace(), feature_type="report", completion=completion, messages=messages)
    error = caught.value
    assert error.retryable and error.provider_io_outcome == "response_received"
    assert error.total_tokens == 30 and error.latency_ms == 42

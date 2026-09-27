import copy
from app.services.ai_quality_evaluation import evaluate_predictions
from app.services.ai_quality_gates import QualityThresholds, claim_inventory, dataset_case_digest, promotion_gate
from tests.unit.test_ai_quality_evaluation import fixture


def approved():
    dataset, digest, prediction = fixture()
    case = dataset["cases"][0]
    case.update(review_status="analyst_approved", reviewed_by="unit reviewer", reviewed_at="2026-01-01T00:00:00Z")
    case["reviewed_sha256"] = dataset_case_digest(case)
    prediction["review"] = {"reviewer": "unit reviewer", "reviewed_at": "2026-01-01T00:00:00Z", "hunt_usefulness": 4,
        "claims": [{"claim_id": claim["claim_id"], "verdict": "supported", "rationale": "Matches the supplied exact evidence."}
                   for claim in claim_inventory(prediction)]}
    return dataset, digest, prediction


def test_complete_exact_approvals_pass_configured_gates():
    dataset, digest, prediction = approved()
    report = evaluate_predictions(dataset, digest, [prediction])
    assert promotion_gate(dataset, [prediction], report, QualityThresholds())["passed"]


def test_changed_case_or_claim_content_invalidates_approval():
    dataset, digest, prediction = approved()
    dataset["cases"][0]["source"]["title"] = "Changed evidence"
    prediction["structured_extraction"]["entities"][0]["description"] = "Changed interpretation"
    report = evaluate_predictions(dataset, digest, [prediction])
    gate = promotion_gate(dataset, [prediction], report, QualityThresholds())
    assert not gate["passed"]
    assert any("content revision" in failure for failure in gate["failures"])
    assert any("claim judgments" in failure for failure in gate["failures"])


def test_anonymous_counts_cannot_substitute_for_identified_claim_judgments():
    dataset, digest, prediction = approved()
    prediction["review"].pop("claims")
    prediction["review"]["claim_verdicts"] = ["supported"]
    report = evaluate_predictions(dataset, digest, [prediction])
    assert not promotion_gate(dataset, [prediction], report, QualityThresholds())["passed"]


def test_missing_cost_latency_and_unsupported_claims_fail_closed():
    dataset, digest, prediction = approved()
    prediction.update(prompt_tokens=None, latency_ms=None)
    prediction["review"]["claims"][0]["verdict"] = "unsupported"
    report = evaluate_predictions(dataset, digest, [prediction])
    failures = promotion_gate(dataset, [prediction], report, QualityThresholds())["failures"]
    assert any("unsupported_claim_rate" in failure for failure in failures)
    assert any("known_cost_usd" in failure for failure in failures)
    assert any("latency_p95_ms" in failure for failure in failures)


def test_duplicate_claim_judgments_fail_even_with_inflated_supported_count():
    dataset, digest, prediction = approved()
    prediction["review"]["claims"].append(copy.deepcopy(prediction["review"]["claims"][0]))
    report = evaluate_predictions(dataset, digest, [prediction])
    assert not promotion_gate(dataset, [prediction], report, QualityThresholds())["passed"]


def test_forged_reviewed_status_and_future_review_time_do_not_qualify():
    dataset, digest, prediction = approved()
    dataset["cases"][0].pop("reviewed_sha256")
    prediction["review"]["reviewed_at"] = "2999-01-01T00:00:00Z"
    report = evaluate_predictions(dataset, digest, [prediction])
    assert not promotion_gate(dataset, [prediction], report, QualityThresholds())["passed"]


def test_malformed_provider_shape_is_a_failed_evaluation_not_a_crash():
    dataset, digest, prediction = approved()
    prediction["structured_extraction"] = "Malformed provider text"
    report = evaluate_predictions(dataset, digest, [prediction])
    assert report["comparisons"][0]["validation_pass_rate"] == 0
    assert not promotion_gate(dataset, [prediction], report, QualityThresholds())["passed"]

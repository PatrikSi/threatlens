import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.services.ai_quality_evaluation import evaluate_predictions, load_dataset, load_predictions
from tests.unit.test_ai_extraction import ARTICLE, extraction_payload

CORPUS = Path(__file__).resolve().parents[2] / "evaluations/ai-quality/v1.json"


def fixture():
    extraction = extraction_payload()
    case = {"id": "case", "review_status": "pending_analyst_review", "source": {
        "title": "Research report", "summary": "", "article_text": ARTICLE,
    }, "expected_entities": extraction["entities"]}
    dataset = {"schema_version": 1, "dataset_version": "unit", "cases": [case]}
    digest = hashlib.sha256(json.dumps(dataset).encode()).hexdigest()
    prediction = {"case_id": "case", "dataset_sha256": digest, "model": "model-v1",
        "prompt_version": "prompt-v1", "structured_extraction": extraction,
        "latency_ms": 100, "prompt_tokens": 1000, "completion_tokens": 200,
        "input_usd_per_million": 1, "output_usd_per_million": 2}
    return dataset, digest, prediction


def test_quality_comparison_does_not_invent_human_scores():
    dataset, digest, prediction = fixture()
    result = evaluate_predictions(dataset, digest, [prediction])
    row = result["comparisons"][0]
    assert row["fixture_entity_precision"] == row["fixture_entity_recall"] == 1
    assert row["unsupported_claim_rate"] is None
    assert row["mean_hunt_usefulness_0_to_4"] is None
    assert row["known_cost_usd"] == pytest.approx(.0014)
    assert row["latency_p95_ms"] == 100
    assert not result["dataset_analyst_approved"]


def test_explicit_analyst_judgments_and_missing_usage_remain_distinguishable():
    dataset, digest, prediction = fixture()
    prediction.update(prompt_tokens=None, latency_ms=None, review={
        "reviewer": "test analyst", "reviewed_at": "2026-09-26T00:00:00Z",
        "claim_verdicts": ["supported", "unsupported", "uncertain"], "hunt_usefulness": 2,
    })
    row = evaluate_predictions(dataset, digest, [prediction])["comparisons"][0]
    assert row["unsupported_claim_rate"] == pytest.approx(1 / 3)
    assert row["uncertain_claim_rate"] == pytest.approx(1 / 3)
    assert row["mean_hunt_usefulness_0_to_4"] == 2
    assert row["known_cost_usd"] is None
    assert row["latency_p95_ms"] is None


@pytest.mark.parametrize("change", [
    {"dataset_sha256": "wrong"}, {"case_id": "unknown"}, {"model": ""},
    {"latency_ms": float("nan")}, {"prompt_tokens": -1}, {"completion_tokens": True},
    {"input_usd_per_million": float("inf")}, {"completion_tokens": .5},
    {"review": {"hunt_usefulness": 4}},
    {"review": {"reviewer": "analyst", "reviewed_at": "today", "claim_verdicts": ["guess"]}},
    {"review": {"reviewer": "analyst", "reviewed_at": "today", "hunt_usefulness": 5}},
])
def test_invalid_evaluation_metadata_is_rejected(change):
    dataset, digest, prediction = fixture()
    prediction.update(change)
    with pytest.raises(ValueError):
        evaluate_predictions(dataset, digest, [prediction])


def test_duplicate_predictions_and_omissions_cannot_hide_missing_coverage():
    dataset, digest, prediction = fixture()
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate_predictions(dataset, digest, [prediction, prediction])
    dataset["cases"].append({**copy.deepcopy(dataset["cases"][0]), "id": "missing"})
    row = evaluate_predictions(dataset, digest, [prediction])["comparisons"][0]
    assert row["cases_missing"] == ["missing"]


def test_invalid_evidence_counts_as_failed_validation():
    dataset, digest, prediction = fixture()
    prediction["structured_extraction"]["entities"][0]["evidence"][0]["quote"] = "This was never in the source."
    row = evaluate_predictions(dataset, digest, [prediction])["comparisons"][0]
    assert row["validation_pass_rate"] == 0
    assert row["fixture_entity_recall"] == 0


def test_seed_corpus_cannot_claim_analyst_release_qualification():
    dataset, digest = load_dataset(CORPUS)
    assert len(dataset["cases"]) == 12
    assert len(digest) == 64
    assert {category for case in dataset["cases"] for category in case["categories"]} >= {
        "reference_domains", "conflicting_claims", "attribution", "incomplete_evidence", "prompt_injection",
    }
    with pytest.raises(ValueError, match="pending analyst review"):
        load_dataset(CORPUS, require_reviewed=True)


def test_prediction_artifacts_are_bounded(tmp_path):
    path = tmp_path / "predictions.jsonl"
    path.write_text("{}\n" * 2001)
    with pytest.raises(ValueError, match="2,000"):
        load_predictions(path)
    path.write_bytes(b" " * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="10 MiB"):
        load_predictions(path)

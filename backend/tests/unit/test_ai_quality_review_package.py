"""Review forms bind exact evidence, preserve captures and never grant approval."""

import copy
import json

import pytest

from app.services.ai_quality_evaluation import evaluate_predictions, load_dataset
from app.services.ai_quality_gates import claim_inventory
from app.services.ai_quality_review_package import prepare_review_package
from tests.unit.test_ai_quality_evaluation import CORPUS, fixture


def test_all_seed_cases_are_reproducible_and_remain_unapproved(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    manifest = prepare_review_package(CORPUS, first)
    prepare_review_package(CORPUS, second)
    assert manifest["dataset_analyst_approved"] is False
    assert manifest["captured_outputs_supplied"] is False
    assert len(manifest["cases"]) == 12 and manifest["model_coverage"] == []
    assert {case["review_status"] for case in manifest["cases"]} == {"pending_analyst_review"}
    assert (first / "dataset.json").read_bytes() == CORPUS.read_bytes()
    files = [path.relative_to(first) for path in first.rglob("*") if path.is_file()]
    assert all((first / name).read_bytes() == (second / name).read_bytes() for name in files)
    assert all((first / case["review_file"]).is_file() for case in manifest["cases"])
    reviews = [json.loads(line) for line in (first / "reviews.template.jsonl").read_text().splitlines()]
    assert len(reviews) == 12
    assert all(row["structured_extraction"] is None and row["review"]["reviewer"] is None
               and row["review"]["claims"] == [] for row in reviews)
    with pytest.raises(ValueError, match="pending analyst review"):
        load_dataset(first / "dataset.json", require_reviewed=True)


def test_captured_output_forms_identify_actual_claims_and_disclose_missing_cases(tmp_path):
    dataset, _digest, prediction = fixture()
    dataset["cases"].append({**copy.deepcopy(dataset["cases"][0]), "id": "missing"})
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(dataset))
    _dataset, digest = load_dataset(source)
    prediction["dataset_sha256"] = digest
    captured = tmp_path / "captured.jsonl"
    captured.write_text(json.dumps(prediction) + "\n")
    package = tmp_path / "package"
    manifest = prepare_review_package(source, package, predictions_path=captured)
    assert (package / "captured-predictions.jsonl").read_bytes() == captured.read_bytes()
    assert manifest["captured_outputs_supplied"] is True
    assert manifest["model_coverage"][0]["cases_missing"] == ["missing"]
    review = json.loads((package / "reviews.template.jsonl").read_text())
    assert review["structured_extraction"] == prediction["structured_extraction"]
    assert {entry["claim_id"] for entry in review["review"]["claims"]} == {
        entry["claim_id"] for entry in claim_inventory(prediction)}
    assert all(entry["verdict"] is None and entry["rationale"] == "" for entry in review["review"]["claims"])
    with pytest.raises(ValueError, match="provenance"):
        evaluate_predictions(dataset, digest, [review])


def test_existing_package_cannot_overwrite_human_work(tmp_path):
    package = tmp_path / "review"
    prepare_review_package(CORPUS, package)
    notes = package / "reviewer-notes.txt"
    notes.write_text("Personally reviewed corrections")
    before = (package / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        prepare_review_package(CORPUS, package)
    assert notes.read_text() == "Personally reviewed corrections"
    assert (package / "manifest.json").read_bytes() == before


def test_captures_from_other_dataset_revision_are_rejected_before_writing(tmp_path):
    _dataset, _digest, prediction = fixture()
    captured = tmp_path / "captured.jsonl"
    captured.write_text(json.dumps(prediction) + "\n")
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError):
        prepare_review_package(CORPUS, destination, predictions_path=captured)
    assert not destination.exists()


def test_case_ids_cannot_escape_the_package(tmp_path):
    dataset, _digest, _prediction = fixture()
    dataset["cases"][0]["id"] = "../../escape"
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(dataset))
    output = tmp_path / "package"
    manifest = prepare_review_package(source, output)
    path = output / manifest["cases"][0]["review_file"]
    assert path.is_file() and path.resolve().is_relative_to(output.resolve())

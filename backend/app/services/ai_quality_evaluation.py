"""Offline, versioned extraction checks and explicitly human-scored claim quality.

Fixture agreement is a regression signal, never proof of factual truth. Human
interpretation and hunt usefulness scores are reported only when supplied with
named review provenance; this module never invents analyst judgments.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

from app.services.ai_extraction import ExtractionValidationError, validate_structured_extraction
from app.services.ai_quality_gates import dataset_case_approved, valid_review_identity

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_PREDICTIONS = 2_000


def load_dataset(path: Path, *, require_reviewed: bool = False) -> tuple[dict, str]:
    raw = _read_bounded(path)
    dataset = json.loads(raw)
    if dataset.get("schema_version") != 1 or not dataset.get("dataset_version"):
        raise ValueError("Unsupported evaluation dataset schema or missing version.")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise ValueError("Evaluation datasets require between 1 and 200 cases.")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in seen:
            raise ValueError("Evaluation case IDs must be unique strings.")
        seen.add(case["id"])
        if not isinstance(case.get("source"), dict) or not isinstance(case.get("expected_entities"), list):
            raise ValueError("Every case requires source fields and expected entity annotations.")
        if require_reviewed and not dataset_case_approved(case):
            raise ValueError(f"Case {case['id']} is pending analyst review; release qualification is blocked.")
    return dataset, hashlib.sha256(raw).hexdigest()


def load_predictions(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in _read_bounded(path).decode("utf-8").splitlines() if line.strip()]
    if not 1 <= len(rows) <= MAX_PREDICTIONS or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Prediction artifacts require between 1 and 2,000 JSON objects.")
    return rows


def evaluate_predictions(dataset: dict, dataset_sha256: str, predictions: list[dict]) -> dict:
    cases = {case["id"]: case for case in dataset["cases"]}
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seen = set()
    for prediction in predictions:
        case_id = prediction.get("case_id")
        model = prediction.get("model")
        prompt = prediction.get("prompt_version")
        if case_id not in cases or not isinstance(model, str) or not model or not isinstance(prompt, str) or not prompt:
            raise ValueError("Each prediction requires a known case ID, model and prompt_version.")
        if prediction.get("dataset_sha256") != dataset_sha256:
            raise ValueError("Prediction evidence does not match the exact dataset revision.")
        identity = (model, prompt, case_id)
        if identity in seen:
            raise ValueError("Duplicate model/prompt/case prediction would distort the evaluation denominator.")
        seen.add(identity)
        case = cases[case_id]
        expected = {_entity_key(entity) for entity in case["expected_entities"]}
        validation_error = False
        try:
            extraction = validate_structured_extraction(prediction.get("structured_extraction"), source=case["source"])
            actual = {_entity_key(entity) for entity in extraction["entities"]}
        except ExtractionValidationError:
            actual = set()
            validation_error = True
        latency = _number(prediction.get("latency_ms"), "latency_ms")
        prompt_tokens = _number(prediction.get("prompt_tokens"), "prompt_tokens", integer=True)
        completion_tokens = _number(prediction.get("completion_tokens"), "completion_tokens", integer=True)
        input_price = _number(prediction.get("input_usd_per_million"), "input_usd_per_million")
        output_price = _number(prediction.get("output_usd_per_million"), "output_usd_per_million")
        known_cost = all(value is not None for value in (prompt_tokens, completion_tokens, input_price, output_price))
        cost = (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000 if known_cost else None
        review = _review(prediction.get("review"))
        from app.services.ai_quality_gates import claim_inventory
        claims = claim_inventory(prediction)
        groups[(model, prompt)].append({
            "case_id": case_id, "categories": case.get("categories", []),
            "validation_passed": not validation_error,
            "matched_entities": len(actual & expected), "expected_entities": len(expected),
            "predicted_entities": len(actual), "unexpected_entities": len(actual - expected),
            "latency_ms": latency, "cost_usd": cost, "review": review, "claims": claims,
        })
    comparisons = []
    for (model, prompt), rows in sorted(groups.items()):
        matched = sum(row["matched_entities"] for row in rows)
        expected = sum(row["expected_entities"] for row in rows)
        predicted = sum(row["predicted_entities"] for row in rows)
        verdicts = [verdict for row in rows if row["review"] for verdict in row["review"]["claim_verdicts"]]
        usefulness = [row["review"]["hunt_usefulness"] for row in rows
                      if row["review"] and row["review"]["hunt_usefulness"] is not None]
        latencies = sorted(row["latency_ms"] for row in rows if row["latency_ms"] is not None)
        costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
        comparisons.append({
            "model": model, "prompt_version": prompt, "cases_evaluated": len(rows),
            "cases_missing": sorted(set(cases) - {row["case_id"] for row in rows}),
            "validation_pass_rate": sum(row["validation_passed"] for row in rows) / len(rows),
            "fixture_entity_precision": matched / predicted if predicted else None,
            "fixture_entity_recall": matched / expected if expected else None,
            "analyst_reviewed_cases": sum(row["review"] is not None for row in rows),
            "reviewed_claims": len(verdicts),
            "supported_claim_rate": verdicts.count("supported") / len(verdicts) if verdicts else None,
            "unsupported_claim_rate": verdicts.count("unsupported") / len(verdicts) if verdicts else None,
            "uncertain_claim_rate": verdicts.count("uncertain") / len(verdicts) if verdicts else None,
            "mean_hunt_usefulness_0_to_4": mean(usefulness) if usefulness else None,
            "latency_samples": len(latencies), "latency_p50_ms": _percentile(latencies, .5),
            "latency_p95_ms": _percentile(latencies, .95),
            "known_cost_usd": sum(costs) if costs else None, "cost_samples": len(costs), "cases": rows,
        })
    approved = all(dataset_case_approved(case) for case in cases.values())
    return {
        "schema_version": 1, "dataset_version": dataset["dataset_version"], "dataset_sha256": dataset_sha256,
        "dataset_analyst_approved": bool(approved), "comparisons": comparisons,
        "limitations": [
            "Fixture agreement does not establish semantic correctness or factual truth.",
            "Claim support and hunt usefulness require recorded analyst judgments; missing scores remain null.",
            "Seed cases are AI-authored and pending analyst review unless explicitly approved in the corpus.",
            "Compare only complete case coverage with equivalent source revisions and disclosed prices.",
        ],
    }


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("Evaluation artifact exceeds the 10 MiB limit.")
    return raw


def _entity_key(entity: dict) -> tuple:
    return (entity.get("kind"), str(entity.get("name", "")).casefold(), entity.get("assertion"), entity.get("indicator_role"))


def _number(value: object, name: str, *, integer: bool = False) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number or null.")
    if integer and int(value) != value:
        raise ValueError(f"{name} must be an integer.")
    return value


def _review(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not valid_review_identity(value.get("reviewer"), value.get("reviewed_at")):
        raise ValueError("Analyst scores require named reviewer and valid, past timezone-aware reviewed_at provenance.")
    identified = value.get("claims")
    if identified is not None and (not isinstance(identified, list) or not all(isinstance(entry, dict) for entry in identified)):
        raise ValueError("Identified claim judgments must be objects.")
    verdicts = [entry.get("verdict") for entry in identified] if identified is not None else value.get("claim_verdicts", [])
    if not isinstance(verdicts, list) or not all(verdict in ("supported", "unsupported", "uncertain") for verdict in verdicts):
        raise ValueError("Claim verdicts must be supported, unsupported or uncertain.")
    usefulness = _number(value.get("hunt_usefulness"), "hunt_usefulness", integer=True)
    if usefulness is not None and usefulness > 4:
        raise ValueError("Hunt usefulness must be between 0 and 4.")
    return {"reviewer": value["reviewer"], "reviewed_at": value["reviewed_at"],
            "claim_verdicts": verdicts, "hunt_usefulness": usefulness}


def _percentile(values: list, fraction: float) -> float | None:
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None

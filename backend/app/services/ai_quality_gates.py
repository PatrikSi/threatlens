"""Fail-closed promotion gates for immutable, fully reviewed evaluation artifacts."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field


class QualityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    minimum_validation_rate: float = Field(default=1, ge=0, le=1)
    minimum_entity_precision: float = Field(default=.95, ge=0, le=1)
    minimum_entity_recall: float = Field(default=.90, ge=0, le=1)
    maximum_unsupported_claim_rate: float = Field(default=0, ge=0, le=1)
    minimum_hunt_usefulness: float = Field(default=3, ge=0, le=4)
    maximum_p95_latency_ms: float = Field(default=60000, gt=0)
    maximum_total_cost_usd: float = Field(default=1, ge=0)


def claim_inventory(prediction: dict) -> list[dict]:
    """Claim IDs bind the judgment to exact output content, independent of order."""
    extraction = prediction.get("structured_extraction")
    if not isinstance(extraction, dict):
        return []
    claims = []
    for kind in ("entities", "relationships"):
        entries = extraction.get(kind, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            content = {"kind": kind, "claim": entry}
            digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            claims.append({"claim_id": digest, **content})
    return claims


def reviewed_claims(prediction: dict) -> bool:
    review = prediction.get("review") or {}
    judgments = review.get("claims")
    expected = {entry["claim_id"] for entry in claim_inventory(prediction)}
    if not isinstance(judgments, list):
        return False
    actual = set()
    for judgment in judgments:
        if (not isinstance(judgment, dict) or judgment.get("claim_id") in actual
                or judgment.get("claim_id") not in expected
                or judgment.get("verdict") not in {"supported", "unsupported", "uncertain"}
                or not isinstance(judgment.get("rationale"), str) or not judgment["rationale"].strip()):
            return False
        actual.add(judgment["claim_id"])
    return actual == expected and valid_review_identity(review.get("reviewer"), review.get("reviewed_at"))


def valid_review_identity(reviewer: object, reviewed_at: object) -> bool:
    try:
        timestamp = datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
        return bool(isinstance(reviewer, str) and reviewer.strip() and timestamp.tzinfo
                    and timestamp <= datetime.now(timezone.utc))
    except (ValueError, TypeError):
        return False


def dataset_case_digest(case: dict) -> str:
    content = {key: value for key, value in case.items()
               if key not in {"review_status", "reviewed_by", "reviewed_at", "reviewed_sha256"}}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def dataset_case_approved(case: dict) -> bool:
    return (case.get("review_status") == "analyst_approved"
            and valid_review_identity(case.get("reviewed_by"), case.get("reviewed_at"))
            and case.get("reviewed_sha256") == dataset_case_digest(case))


def promotion_gate(dataset: dict, predictions: list[dict], report: dict, thresholds: QualityThresholds) -> dict:
    failures = []
    for case in dataset["cases"]:
        if not dataset_case_approved(case):
            failures.append(f"Dataset case {case['id']} lacks approval of its exact content revision.")
    for prediction in predictions:
        if not reviewed_claims(prediction):
            failures.append(f"{prediction['model']}/{prediction['prompt_version']}/{prediction['case_id']}: exact claim judgments are incomplete.")
    for comparison in report["comparisons"]:
        label = f"{comparison['model']}/{comparison['prompt_version']}"
        if comparison["cases_missing"]:
            failures.append(f"{label}: dataset cases are missing.")
        checks = [
            ("validation_pass_rate", thresholds.minimum_validation_rate, True),
            ("fixture_entity_precision", thresholds.minimum_entity_precision, True),
            ("fixture_entity_recall", thresholds.minimum_entity_recall, True),
            ("unsupported_claim_rate", thresholds.maximum_unsupported_claim_rate, False),
            ("mean_hunt_usefulness_0_to_4", thresholds.minimum_hunt_usefulness, True),
            ("latency_p95_ms", thresholds.maximum_p95_latency_ms, False),
            ("known_cost_usd", thresholds.maximum_total_cost_usd, False),
        ]
        for name, threshold, minimum in checks:
            value = comparison[name]
            if value is None or (value < threshold if minimum else value > threshold):
                failures.append(f"{label}: {name} is missing or outside the required threshold.")
        count = comparison["cases_evaluated"]
        if any(comparison[key] != count for key in ("cost_samples", "latency_samples", "analyst_reviewed_cases")):
            failures.append(f"{label}: cost, latency or analyst review coverage is incomplete.")
    if not report["comparisons"]:
        failures.append("No model/prompt comparisons were supplied.")
    return {"passed": not failures, "failures": failures, "thresholds": thresholds.model_dump(),
            "requires_human_review": True, "dataset_sha256": report["dataset_sha256"]}

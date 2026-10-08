"""Prepare immutable offline evidence and blank human-review forms."""

import copy
import hashlib
import json
from pathlib import Path
import re

from app.services.ai_quality_evaluation import (
    _read_bounded,
    evaluate_predictions,
    load_dataset,
    load_predictions,
)
from app.services.ai_quality_gates import (
    QualityThresholds,
    claim_inventory,
    dataset_case_approved,
    dataset_case_digest,
)


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def _input_rows(dataset, digest):
    return [{"case_id": case["id"], "dataset_sha256": digest,
             "model": "REPLACE", "prompt_version": "REPLACE", "source": case["source"],
             "structured_extraction": None, "latency_ms": None, "prompt_tokens": None,
             "completion_tokens": None, "input_usd_per_million": None,
             "output_usd_per_million": None, "review": None} for case in dataset["cases"]]


def prepare_review_package(dataset_path: Path, output: Path, *, predictions_path: Path | None = None) -> dict:
    dataset, digest = load_dataset(dataset_path)
    dataset_bytes = _read_bounded(dataset_path)
    if hashlib.sha256(dataset_bytes).hexdigest() != digest:
        raise ValueError("Dataset changed during preparation; retry with a stable revision.")
    inputs = _input_rows(dataset, digest)
    predictions = load_predictions(predictions_path) if predictions_path else inputs
    comparison = evaluate_predictions(dataset, digest, predictions) if predictions_path else None
    reviews = copy.deepcopy(predictions)
    for row in reviews:
        row["review"] = {"reviewer": None, "reviewed_at": None, "hunt_usefulness": None,
                         "claims": [{"claim_id": claim["claim_id"], "verdict": None, "rationale": ""}
                                    for claim in claim_inventory(row)]}
    manifest = {"schema_version": 1, "dataset_version": dataset["dataset_version"],
                "dataset_sha256": digest, "dataset_analyst_approved": all(
                    dataset_case_approved(case) for case in dataset["cases"]),
                "captured_outputs_supplied": bool(predictions_path), "cases": [],
                "model_coverage": [{"model": row["model"], "prompt_version": row["prompt_version"],
                                    "cases_evaluated": row["cases_evaluated"], "cases_missing": row["cases_missing"]}
                                   for row in comparison["comparisons"]] if comparison else []}
    files = {"dataset.json": dataset_bytes, "predictions.template.jsonl": _jsonl(inputs),
             "reviews.template.jsonl": _jsonl(reviews), "thresholds.json": _json(QualityThresholds().model_dump())}
    if predictions_path:
        captured = _read_bounded(predictions_path)
        if [json.loads(line) for line in captured.decode().splitlines() if line.strip()] != predictions:
            raise ValueError("Predictions changed during preparation; retry with a stable artifact.")
        files["captured-predictions.jsonl"] = captured
        manifest["captured_predictions_sha256"] = hashlib.sha256(captured).hexdigest()
    for index, case in enumerate(dataset["cases"], 1):
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", case["id"])[:80] or "case"
        name = f"cases/{index:02}-{slug}.md"
        case_digest = dataset_case_digest(case)
        manifest["cases"].append({"case_id": case["id"], "content_sha256": case_digest,
                                  "review_status": case.get("review_status"), "review_file": name})
        files[name] = (f"# {case['id']}\n\nContent SHA256: `{case_digest}`\n\n"
                       f"Corpus status: `{case.get('review_status', 'unknown')}`.\n\n"
                       f"## Source\n\n````json\n{_json(case['source'])}````\n\n"
                       f"## Expected entities to verify\n\n````json\n{_json(case['expected_entities'])}````\n\n"
                       f"## Analyst rubric\n\n{case.get('analyst_rubric', 'Review the source and expected annotations.')}\n\n"
                       "Record corrections in a new dataset revision before approving. "
                       "No case or model-output judgment is approved by this package.\n")
    files["README.md"] = _instructions(manifest)
    # A fresh directory protects captured results and any human-filled forms.
    output.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    (output / "manifest.json").write_text(_json(manifest), encoding="utf-8")
    return manifest


def _instructions(manifest):
    rows = "\n".join(f"| `{case['case_id']}` | [{case['review_file']}]({case['review_file']}) | `{case['content_sha256']}` |"
                     for case in manifest["cases"])
    captured = "Captured outputs are included unchanged." if manifest["captured_outputs_supplied"] else "No model outputs have been captured."
    return f"""# AI quality review package

This package contains {len(manifest['cases'])} corpus cases from `{manifest['dataset_version']}`.
Its exact dataset SHA256 is `{manifest['dataset_sha256']}`. {captured}
The package records no new analyst approvals, model-quality judgments or paid calls.
`manifest.json` lists exact case digests and any captured model/prompt coverage gaps.

| Case | Review source, annotations and rubric | Content SHA256 |
| --- | --- | --- |
{rows}

1. A named human reviews every source, expected entity and rubric. Correct annotations
   in a new dataset version when needed, then regenerate into a fresh directory.
2. Approve each personally reviewed case with the existing CLI. Substitute the
   selected case ID, its current digest and your actual name. Successive approvals
   must read the last reviewed dataset output so earlier approvals are retained:

   ```bash
   backend/.venv/bin/python backend/scripts/review_ai_quality.py \\
     --dataset PATH/TO/dataset.json --case CASE_ID --reviewer YOUR_NAME \\
     --expected-sha256 CASE_CONTENT_SHA256 --approve --output /tmp/reviewed-dataset.json
   ```

3. Once all cases are reviewed, regenerate the package from that final dataset.
   Approvals change its byte digest; old prediction templates no longer bind to it.
   Use `predictions.template.jsonl` to capture exact model and prompt revisions,
   structured outputs, latency, usage and explicit prices from separately authorized
   application runs. Unknown telemetry stays null. Preparation performs no provider I/O.
4. Prepare a fresh package with `--predictions PATH/TO/captured.jsonl`. It retains
   the original bytes in `captured-predictions.jsonl` and supplies exact claim IDs in
   `reviews.template.jsonl`. Fill reviewer, truthful timezone-aware review time,
   supported/unsupported/uncertain verdict and rationale for every claim, plus
   hunt usefulness (0–4). Empty fields remain pending; blank input templates cannot
   qualify. Preserve the original captured file, and review actual model output
   rather than treating the corpus's expected entities as predictions.
5. Run the promotion gate on the human-completed file. The default thresholds are
   included in `thresholds.json`; any policy changes require independent review:

   ```bash
   backend/.venv/bin/python backend/scripts/evaluate_ai_quality.py \\
     --dataset PATH/TO/dataset.json --predictions PATH/TO/completed-reviews.jsonl \\
     --require-reviewed --gate --thresholds PATH/TO/thresholds.json \\
     --output /tmp/ai-quality-comparison.json
   ```

Exit 2 indicates invalid/incomplete input; exit 3 indicates an unmet promotion gate.
Passing a contract probe or generating this package does not establish semantic
quality or change production routing. A CLI attestation does not independently
authenticate the reviewer; retain normal review provenance with the artifacts.
"""

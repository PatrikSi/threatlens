#!/usr/bin/env python3
"""Score captured provider results without sending evidence or consuming API quota."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ai_quality_evaluation import evaluate_predictions, load_dataset, load_predictions  # noqa: E402
from app.services.ai_quality_gates import QualityThresholds, promotion_gate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "evaluations/ai-quality/v1.json")
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-reviewed", action="store_true", help="Reject any corpus case lacking analyst approval.")
    parser.add_argument("--prepare", action="store_true", help="Write case input/template JSONL instead of scoring.")
    parser.add_argument("--gate", action="store_true", help="Fail closed unless exact dataset/claim approvals and thresholds pass.")
    parser.add_argument("--thresholds", type=Path, help="JSON QualityThresholds; unknown settings are rejected.")
    args = parser.parse_args()
    gate_failed = False
    try:
        dataset, digest = load_dataset(args.dataset, require_reviewed=args.require_reviewed)
        if args.prepare:
            lines = [json.dumps({"case_id": case["id"], "dataset_sha256": digest,
                "model": "REPLACE", "prompt_version": "REPLACE", "source": case["source"],
                "structured_extraction": None, "latency_ms": None, "prompt_tokens": None,
                "completion_tokens": None, "input_usd_per_million": None,
                "output_usd_per_million": None, "review": None}, ensure_ascii=False) for case in dataset["cases"]]
            args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            if args.predictions is None:
                parser.error("--predictions is required unless --prepare is selected")
            predictions = load_predictions(args.predictions)
            result = evaluate_predictions(dataset, digest, predictions)
            if args.gate:
                thresholds = QualityThresholds.model_validate_json(args.thresholds.read_text()) if args.thresholds else QualityThresholds()
                result["promotion_gate"] = promotion_gate(dataset, predictions, result, thresholds)
                gate_failed = not result["promotion_gate"]["passed"]
            args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 2
    return 3 if gate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

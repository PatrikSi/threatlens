#!/usr/bin/env python3
"""Score captured provider results without sending evidence or consuming API quota."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ai_quality_evaluation import evaluate_predictions, load_dataset, load_predictions  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "evaluations/ai-quality/v1.json")
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-reviewed", action="store_true", help="Reject any corpus case lacking analyst approval.")
    parser.add_argument("--prepare", action="store_true", help="Write case input/template JSONL instead of scoring.")
    args = parser.parse_args()
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
            result = evaluate_predictions(dataset, digest, load_predictions(args.predictions))
            args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

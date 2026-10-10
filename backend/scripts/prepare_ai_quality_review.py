#!/usr/bin/env python3
"""Prepare offline corpus and captured-output review forms without approving them."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ai_quality_review_package import prepare_review_package  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[1] / "evaluations/ai-quality/v1.json")
    parser.add_argument("--predictions", type=Path, help="Optional captured JSONL, bound to this exact dataset digest.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Fresh directory; existing review packages are never overwritten.")
    args = parser.parse_args()
    try:
        manifest = prepare_review_package(args.dataset, args.output_dir, predictions_path=args.predictions)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Review package not prepared: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared {len(manifest['cases'])} cases at {args.output_dir}; human case and model-output review remain required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

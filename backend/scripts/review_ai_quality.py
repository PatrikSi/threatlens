#!/usr/bin/env python3
"""Record an operator's explicit case approval; never approve cases automatically."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ai_quality_evaluation import load_dataset  # noqa: E402
from app.services.ai_quality_gates import dataset_case_digest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--expected-sha256", required=True, help="Digest of the exact case content personally reviewed.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approve", action="store_true", help="Explicitly attest personal review of the selected case.")
    args = parser.parse_args()
    try:
        dataset, _ = load_dataset(args.dataset)
        case = next((entry for entry in dataset["cases"] if entry["id"] == args.case), None)
        if case is None:
            raise ValueError("Unknown case ID")
        digest = dataset_case_digest(case)
        if not args.approve or not args.reviewer.strip() or args.expected_sha256 != digest:
            raise ValueError(f"Approval requires personal review and the current case digest: {digest}")
        case.update(review_status="analyst_approved", reviewed_by=args.reviewer.strip(),
                    reviewed_at=datetime.now(timezone.utc).isoformat(), reviewed_sha256=digest)
        args.output.write_text(json.dumps(dataset, indent=2, ensure_ascii=False) + "\n")
    except (ValueError, OSError) as exc:
        print(f"Review not recorded: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

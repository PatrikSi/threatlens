#!/usr/bin/env python3
"""Compare compatible capacity artifacts; refuse misleading comparisons."""

import argparse
import json
from pathlib import Path

from capacity_results import compare_results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--regression-percent", type=float, default=20)
    args = parser.parse_args()
    if not 0 <= args.regression_percent <= 1000:
        parser.error("regression percent must be between 0 and 1000")
    try:
        result = compare_results(
            json.loads(args.baseline.read_text()),
            json.loads(args.candidate.read_text()),
            regression_percent=args.regression_percent,
        )
    except (ValueError, KeyError) as exc:
        parser.exit(2, str(exc) + "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not result["compatible"]:
        return 2
    if result["regressions"]:
        return 1
    return 0 if result["conclusive"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Keep paste-only Compose database initialization aligned with the source script."""
from __future__ import annotations

import argparse
import base64
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[2]
BEGIN = "        # BEGIN generated database initialization; do not edit\n"
END = "        # END generated database initialization\n"


def synchronized_compose(compose: str, source: bytes) -> str:
    if compose.count(BEGIN) != 1 or compose.count(END) != 1:
        raise ValueError("Compose must contain exactly one database initialization marker pair")
    start = compose.index(BEGIN) + len(BEGIN)
    end = compose.index(END)
    if end < start:
        raise ValueError("Compose database initialization markers are out of order")
    encoded = base64.b64encode(source).decode("ascii")
    block = (
        "        {\n"
        "          printf '(\\n'\n"
        "          base64 --decode <<'THREATLENS_DATABASE_ROLES'\n"
        + "".join(f"        {line}\n" for line in textwrap.wrap(encoded, width=76))
        + "        THREATLENS_DATABASE_ROLES\n"
        "          printf '\\n)\\n'\n"
        "        } > /docker-entrypoint-initdb.d/10-threatlens-roles.sh\n"
    )
    return compose[:start] + block + compose[end:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the embedded script needs regeneration")
    args = parser.parse_args()
    path = ROOT / "docker-compose.yml"
    original = path.read_text(encoding="utf-8")
    try:
        updated = synchronized_compose(
            original, (ROOT / "scripts/database/provision-roles.sh").read_bytes(),
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if original == updated:
        return 0
    if args.check:
        print(
            "Compose database initialization is stale. Run: python3 scripts/database/sync-compose-init.py",
            file=sys.stderr,
        )
        return 1
    path.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

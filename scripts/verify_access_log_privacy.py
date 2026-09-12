#!/usr/bin/env python3
"""Exercise real proxy/API access logs with synthetic sensitive query values."""

from __future__ import annotations

import argparse
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def verify(base_url: str, containers: list[str]) -> None:
    reference = f"privacy-probe-{uuid.uuid4()}"
    sensitive = [f"private-{name}-{uuid.uuid4()}" for name in ("code", "state", "search", "referrer")]
    since = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    query = urlencode(dict(zip(("code", "state", "q"), sensitive[:3], strict=True)))
    request = Request(
        f"{base_url.rstrip('/')}/api/v1/health?{query}",
        headers={"X-Request-ID": reference, "Referer": f"https://example.invalid/?q={sensitive[3]}"},
    )
    with urlopen(request, timeout=15) as response:
        if response.status != 200 or response.headers.get("X-Request-ID") != reference:
            raise RuntimeError("Privacy probe did not receive the correlated health response")
    pending = set(containers)
    deadline = time.monotonic() + 5
    while pending and time.monotonic() < deadline:
        for container in list(pending):
            result = subprocess.run(
                ["docker", "logs", "--since", since, container],
                capture_output=True, text=True, check=True, timeout=10,
            )
            logs = result.stdout + result.stderr
            if any(value in logs for value in sensitive):
                # Never print the captured logs; deployments can contain unrelated
                # sensitive records from older versions or custom configuration.
                raise RuntimeError(f"Sensitive query/referrer value appeared in access logs for {container}")
            if reference in logs:
                pending.remove(container)
        if pending:
            time.sleep(0.1)
    if pending:
        raise RuntimeError(f"Correlated access records missing for {', '.join(sorted(pending))}")
    print(f"Access-log privacy passed for {len(containers)} containers")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--container", action="append", required=True)
    args = parser.parse_args()
    verify(args.base_url, args.container)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Exercise real proxy/API logs with synthetic sensitive query values and failures."""

from __future__ import annotations

import argparse
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _request_reference(request: Request, *, expected_statuses: set[int]) -> str:
    try:
        response = urlopen(request, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        if response.status not in expected_statuses:
            raise RuntimeError(f"Privacy probe received unexpected HTTP {response.status}")
        reference = response.headers.get("X-Request-ID")
        if not reference:
            raise RuntimeError("Privacy probe response is missing a request reference")
        return reference


def verify(base_url: str, containers: list[str], *, upstream_unavailable: bool = False) -> None:
    reference = f"privacy-probe-{uuid.uuid4()}"
    sensitive = [f"private-{name}-{uuid.uuid4()}" for name in ("code", "state", "search", "referrer")]
    since = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    query = urlencode(dict(zip(("code", "state", "q"), sensitive[:3], strict=True)))
    headers = {"X-Request-ID": reference, "Referer": f"https://example.invalid/?q={sensitive[3]}"}
    origin = base_url.rstrip("/")
    if upstream_unavailable:
        # The caller stops only its isolated API first. This mode performs no
        # container mutations and should receive only the web container as input.
        request = Request(f"{origin}/api/v1/auth/oidc/callback?{query}", headers=headers)
        proxy_reference = _request_reference(request, expected_statuses={502, 504})
    else:
        request = Request(f"{origin}/api/v1/health?{query}", headers=headers)
        if _request_reference(request, expected_statuses={200}) != reference:
            raise RuntimeError("Privacy probe did not receive the correlated health response")
        oversized = Request(
            f"{origin}/api/v1/auth/login?{query}", headers=headers,
            data=b" " * (1_048_576 + 1), method="PUT",
        )
        proxy_reference = _request_reference(oversized, expected_statuses={413})
    pending = set(containers)
    proxy_observed = False
    deadline = time.monotonic() + 5
    while (pending or not proxy_observed) and time.monotonic() < deadline:
        for container in containers:
            result = subprocess.run(
                ["docker", "logs", "--since", since, container],
                capture_output=True, text=True, check=True, timeout=10,
            )
            logs = result.stdout + result.stderr
            if any(value in logs for value in sensitive):
                # Never print captured logs: deployments may contain unrelated
                # sensitive records from older versions or custom configuration.
                raise RuntimeError(f"Sensitive query/referrer value appeared in logs for {container}")
            if proxy_reference in logs:
                proxy_observed = True
            if (proxy_reference if upstream_unavailable else reference) in logs:
                pending.discard(container)
        if pending or not proxy_observed:
            time.sleep(0.1)
    if pending or not proxy_observed:
        raise RuntimeError("Correlated request/failure records are missing from the expected containers")
    mode = "upstream failure" if upstream_unavailable else "health and oversized request"
    print(f"Log privacy passed for {len(containers)} containers ({mode})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--container", action="append", required=True)
    parser.add_argument("--upstream-unavailable", action="store_true")
    args = parser.parse_args()
    verify(args.base_url, args.container, upstream_unavailable=args.upstream_unavailable)


if __name__ == "__main__":
    main()

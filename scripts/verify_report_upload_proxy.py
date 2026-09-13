#!/usr/bin/env python3
"""Check the report-only upload allowance against an isolated running stack."""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def status_for(base_url: str, path: str, body: bytes) -> int:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def verify(base_url: str) -> None:
    path = "/api/v1/reports/00000000-0000-0000-0000-000000000001/draft"
    body = json.dumps({"title": "synthetic-upload-probe-" * 60_000}).encode()
    # No credentials are sent. Reaching the real authorization dependency proves
    # that proxy routing and buffering accepted the larger editorial request.
    status = status_for(base_url, path, body)
    if status not in {401, 403}:
        raise RuntimeError(f"Expected report authorization rejection, received {status}")
    if status_for(base_url, "/api/v1/auth/login", body) != 413:
        raise RuntimeError("The expanded upload allowance escaped its report route")
    if status_for(base_url, path, b" " * (17 * 1024 * 1024)) != 413:
        raise RuntimeError("The report upload allowance did not enforce its wire limit")
    print("Report upload proxy limits passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    verify(parser.parse_args().base_url)

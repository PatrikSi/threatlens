#!/usr/bin/env python3
"""Verify the expected bootstrap credentials against an isolated running stack."""

from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def verify(base_url: str, email: str, password: str) -> None:
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(path: str, payload: dict | None = None, csrf: str | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        body = json.dumps(payload).encode() if payload is not None else None
        try:
            with client.open(
                Request(f"{base_url.rstrip('/')}/api/v1/auth/{path}", data=body, headers=headers),
                timeout=20,
            ) as response:
                content = response.read(65_537)
        except HTTPError as exc:
            # Login responses and cookies are credentials, so report only status.
            raise RuntimeError(f"Bootstrap {path} returned HTTP {exc.code}") from None
        if len(content) > 65_536:
            raise RuntimeError(f"Bootstrap {path} response exceeded its test budget")
        return json.loads(content) if content else {}

    login = request("login", {"email": email, "password": password})
    if login.get("mfa_required") or login.get("token_type") != "session_cookie":
        raise RuntimeError("Fresh administrator did not receive a browser session")
    user = request("me")
    if (
        user.get("email") != email.lower()
        or user.get("role") != "admin"
        or not user.get("is_active")
        or not user.get("is_approved")
    ):
        raise RuntimeError("Bootstrap account is not the expected active administrator")
    request("logout", {}, login.get("csrf_token"))
    print("Bootstrap administrator login, session, and logout passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    arguments = parser.parse_args()
    email = os.environ.get("ADMIN_EMAIL", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        parser.error("Set ADMIN_EMAIL and ADMIN_PASSWORD for this disposable fixture")
    verify(arguments.base_url, email, password)

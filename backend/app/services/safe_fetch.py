from __future__ import annotations

from urllib.parse import urljoin

import httpx

from app.services.url_utils import ensure_runtime_fetchable_url

REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class SafeFetchError(RuntimeError):
    pass


class UnsafeTargetError(SafeFetchError):
    pass


class RedirectError(SafeFetchError):
    pass


def safe_get_with_redirects(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    allow_private_network: bool = True,
    max_redirects: int = 5,
) -> httpx.Response:
    current_url = url
    redirects = 0

    while True:
        _ensure_target(current_url, allow_private_network)
        response = client.get(current_url, headers=headers, follow_redirects=False)
        if response.status_code not in REDIRECT_STATUS_CODES:
            return response

        location = response.headers.get("location")
        response.close()
        if not location:
            raise RedirectError("Redirect missing location header")

        redirects += 1
        if redirects > max_redirects:
            raise RedirectError("Too many redirects")

        current_url = urljoin(current_url, location)


def safe_stream_with_redirects(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    allow_private_network: bool = True,
    max_redirects: int = 5,
) -> httpx.Response:
    current_url = url
    redirects = 0
    current_method = method.upper()

    while True:
        _ensure_target(current_url, allow_private_network)
        request = client.build_request(current_method, current_url, headers=headers)
        response = client.send(request, stream=True, follow_redirects=False)
        if response.status_code not in REDIRECT_STATUS_CODES:
            return response

        location = response.headers.get("location")
        response.close()
        if not location:
            raise RedirectError("Redirect missing location header")

        redirects += 1
        if redirects > max_redirects:
            raise RedirectError("Too many redirects")

        current_url = urljoin(current_url, location)
        if response.status_code == 303 and current_method != "HEAD":
            current_method = "GET"


def _ensure_target(url: str, allow_private_network: bool) -> None:
    try:
        ensure_runtime_fetchable_url(url, allow_private_network=allow_private_network)
    except ValueError as exc:
        raise UnsafeTargetError("URL is not allowed for outbound fetch") from exc

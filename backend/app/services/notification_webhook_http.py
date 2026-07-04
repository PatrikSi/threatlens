from __future__ import annotations

import time
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.core.config import get_settings
from app.schemas.notification import NotificationWebhookField, NotificationWebhookTestResponse
from app.services.safe_fetch import REDIRECT_STATUS_CODES, RedirectError, SafeFetchError, build_safe_http_client
from app.services.url_utils import ensure_runtime_fetchable_url

settings = get_settings()
MAX_RESPONSE_PREVIEW_CHARS = 4000
THREATLENS_DELIVERY_ID_HEADER = "X-ThreatLens-Delivery-ID"
BLOCKED_REQUEST_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "expect",
        "host",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class RenderedNotificationRequestLike(Protocol):
    timeout_seconds: int
    url: str
    method: str
    headers: list[NotificationWebhookField]
    query_params: list[NotificationWebhookField]
    body: str | None
    headers_dict: dict[str, str]
    query_param_pairs: list[tuple[str, str]]
    json_body: dict | None
    form_body: list[tuple[str, str]] | None
    raw_body: bytes | None


def canonical_header_name(header_name: str) -> str:
    return "-".join(part[:1].upper() + part[1:] for part in header_name.split("-"))


def canonicalize_headers(fields: list[NotificationWebhookField]) -> dict[str, str]:
    canonical_headers: dict[str, str] = {}
    seen_names: set[str] = set()

    for field in fields:
        header_name = field.key.strip()
        if not header_name:
            raise ValueError("Header name cannot be empty")

        normalized_name = header_name.lower()
        if normalized_name in BLOCKED_REQUEST_HEADERS:
            raise ValueError(f"Header is not allowed: {header_name}")
        if normalized_name in seen_names:
            raise ValueError(f"Duplicate header: {header_name}")

        seen_names.add(normalized_name)
        canonical_headers[canonical_header_name(header_name)] = field.value

    return canonical_headers


def default_raw_content_type(body_text: str) -> str:
    stripped = body_text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "application/json"
    return "text/plain; charset=utf-8"


def read_response_preview(response: httpx.Response, *, max_bytes: int = MAX_RESPONSE_PREVIEW_CHARS) -> str:
    preview_chunks: list[bytes] = []
    remaining = max_bytes

    for chunk in response.iter_bytes():
        if remaining <= 0:
            break

        if len(chunk) <= remaining:
            preview_chunks.append(chunk)
            remaining -= len(chunk)
            continue

        preview_chunks.append(chunk[:remaining])
        remaining = 0
        break

    return b"".join(preview_chunks).decode("utf-8", errors="replace")


def send_rendered_notification_request(
    rendered: RenderedNotificationRequestLike,
) -> NotificationWebhookTestResponse:
    timeout = httpx.Timeout(
        connect=rendered.timeout_seconds,
        read=rendered.timeout_seconds,
        write=rendered.timeout_seconds,
        pool=rendered.timeout_seconds,
    )
    started_at = time.perf_counter()

    try:
        with build_safe_http_client(
            timeout=timeout,
            headers={"User-Agent": settings.fetch_user_agent},
            allow_private_network=settings.allow_private_network_webhooks,
        ) as client:
            response = send_request_with_redirects(
                client,
                method=rendered.method,
                url=rendered.url,
                headers=rendered.headers_dict,
                params=rendered.query_param_pairs,
                json_body=rendered.json_body,
                form_body=rendered.form_body,
                raw_body=rendered.raw_body,
            )
    except (SafeFetchError, httpx.HTTPError, ValueError) as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        return NotificationWebhookTestResponse(
            success=False,
            status_code=None,
            duration_ms=duration_ms,
            rendered_url=rendered.url,
            rendered_method=rendered.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=None,
            error=str(exc),
        )

    duration_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        response_body_preview = read_response_preview(response, max_bytes=MAX_RESPONSE_PREVIEW_CHARS)
        return NotificationWebhookTestResponse(
            success=200 <= response.status_code < 400,
            status_code=response.status_code,
            duration_ms=duration_ms,
            rendered_url=str(response.request.url),
            rendered_method=response.request.method,
            rendered_headers=rendered.headers,
            rendered_query_params=rendered.query_params,
            rendered_body=rendered.body,
            response_body_preview=response_body_preview,
            error=None if 200 <= response.status_code < 400 else f"HTTP {response.status_code}",
        )
    finally:
        response.close()


def send_request_with_redirects(
    client: httpx.Client,
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    params: list[tuple[str, str]],
    json_body: dict | None,
    form_body: list[tuple[str, str]] | None,
    raw_body: bytes | None,
) -> httpx.Response:
    current_url = url
    current_method = method.upper()
    redirects = 0
    current_json_body = json_body
    current_form_body = form_body
    current_raw_body = raw_body
    current_params = list(params)

    while True:
        ensure_runtime_fetchable_url(current_url, allow_private_network=settings.allow_private_network_webhooks)
        request_url = _merge_request_url(current_url, current_params)
        request = client.build_request(
            current_method,
            request_url,
            headers=headers,
            json=current_json_body,
            data=current_form_body if current_form_body is not None else current_raw_body,
        )
        response = client.send(request, stream=True, follow_redirects=False)
        if response.status_code not in REDIRECT_STATUS_CODES:
            return response

        location = response.headers.get("location")
        if not location:
            response.close()
            raise RedirectError("Redirect missing location header")

        redirects += 1
        if redirects > settings.outbound_max_redirects:
            response.close()
            raise RedirectError("Too many redirects")

        redirect_status = response.status_code
        response.close()
        redirect_url = urljoin(current_url, location)
        if _origin_tuple(redirect_url) != _origin_tuple(current_url):
            raise RedirectError("Cross-origin redirects are not allowed")
        current_url = redirect_url
        current_params = []
        if redirect_status in {301, 302, 303} and current_method not in {"GET", "HEAD"}:
            current_method = "GET"
            current_json_body = None
            current_form_body = None
            current_raw_body = None


def _merge_request_url(url: str, params: list[tuple[str, str]]) -> str:
    if not params:
        return url

    split = urlsplit(url)
    query_pairs = parse_qsl(split.query, keep_blank_values=True)
    query_pairs.extend(params)
    merged_query = urlencode(query_pairs, doseq=True)
    return urlunsplit((split.scheme, split.netloc, split.path, merged_query, split.fragment))


def _origin_tuple(url: str) -> tuple[str, str, int | None]:
    split = urlsplit(url)
    try:
        port = split.port
    except ValueError as exc:
        raise RedirectError("Redirect target URL is invalid") from exc

    scheme = split.scheme.lower()
    if port is None:
        if scheme == "http":
            port = 80
        elif scheme == "https":
            port = 443

    return scheme, (split.hostname or "").lower(), port

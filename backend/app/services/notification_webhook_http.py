from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.core.config import get_settings
from app.schemas.notification import (
    NotificationWebhookField,
    NotificationWebhookTestResponse,
)
from app.services.safe_fetch import (
    REDIRECT_STATUS_CODES,
    RedirectError,
    SafeFetchError,
    build_safe_http_client,
)
from app.services.url_utils import ensure_runtime_fetchable_url

settings = get_settings()
MAX_RESPONSE_PREVIEW_CHARS = 4000
THREATLENS_DELIVERY_ID_HEADER = "X-ThreatLens-Delivery-ID"
_delivery_lease_heartbeat: ContextVar[Callable[[int], None] | None] = ContextVar(
    "notification_delivery_lease_heartbeat",
    default=None,
)
_delivery_external_io_marker: ContextVar[Callable[[], None] | None] = ContextVar(
    "notification_delivery_external_io_marker",
    default=None,
)
_delivery_external_io_started: ContextVar[bool | None] = ContextVar(
    "notification_delivery_external_io_started",
    default=None,
)
_delivery_redirect_chain_started: ContextVar[bool | None] = ContextVar(
    "notification_delivery_redirect_chain_started",
    default=None,
)
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


class WebhookAmbiguousResponseError(RuntimeError):
    code = "ambiguous_webhook_response"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        duration_ms: int = 0,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.duration_ms = duration_ms


@contextmanager
def notification_delivery_lease_heartbeat(
    callback: Callable[[int], None] | None,
) -> Iterator[None]:
    token = _delivery_lease_heartbeat.set(callback)
    try:
        yield
    finally:
        _delivery_lease_heartbeat.reset(token)


@contextmanager
def notification_delivery_external_io_marker(
    callback: Callable[[], None] | None,
) -> Iterator[None]:
    marker_token = _delivery_external_io_marker.set(callback)
    started_token = _delivery_external_io_started.set(
        False if callback is not None else None
    )
    redirect_token = _delivery_redirect_chain_started.set(
        False if callback is not None else None
    )
    try:
        yield
    finally:
        _delivery_redirect_chain_started.reset(redirect_token)
        _delivery_external_io_started.reset(started_token)
        _delivery_external_io_marker.reset(marker_token)


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


def read_response_preview(
    response: httpx.Response,
    *,
    max_bytes: int = MAX_RESPONSE_PREVIEW_CHARS,
    lease_timeout_seconds: int | None = None,
) -> str:
    preview_chunks: list[bytes] = []
    remaining = max_bytes

    for chunk in response.iter_bytes():
        if lease_timeout_seconds is not None:
            _renew_notification_operation_lease(lease_timeout_seconds)
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
    status_code: int | None = None
    request_url = rendered.url
    request_method = rendered.method
    response_body_preview: str | None = None

    try:
        _renew_notification_operation_lease(rendered.timeout_seconds)
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
            try:
                status_code = response.status_code
                request_url = str(response.request.url)
                request_method = response.request.method
                response_body_preview = read_response_preview(
                    response,
                    max_bytes=MAX_RESPONSE_PREVIEW_CHARS,
                    lease_timeout_seconds=rendered.timeout_seconds,
                )
            finally:
                response.close()
    except RedirectError as exc:
        if _delivery_external_io_started.get() is True:
            raise
        return _failed_request_result(rendered, started_at=started_at, error=exc)
    except httpx.RemoteProtocolError as exc:
        if _observed_response_is_final(status_code):
            return _completed_request_result(
                rendered,
                started_at=started_at,
                status_code=status_code,
                request_url=request_url,
                request_method=request_method,
                response_body_preview=_preview_or_unavailable(
                    response_body_preview,
                    error=exc,
                ),
            )
        if _delivery_external_io_started.get() is True:
            raise WebhookAmbiguousResponseError(
                f"Webhook response was invalid after the request began: {exc}",
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            ) from exc
        return _failed_request_result(rendered, started_at=started_at, error=exc)
    except (SafeFetchError, httpx.HTTPError, ValueError) as exc:
        if _observed_response_is_final(status_code):
            return _completed_request_result(
                rendered,
                started_at=started_at,
                status_code=status_code,
                request_url=request_url,
                request_method=request_method,
                response_body_preview=_preview_or_unavailable(
                    response_body_preview,
                    error=exc,
                ),
            )
        if _delivery_redirect_chain_started.get() is True:
            raise RedirectError(
                f"Redirect chain failed after the initial request: {exc}"
            ) from exc
        if _delivery_external_io_started.get() is True:
            raise WebhookAmbiguousResponseError(
                f"Webhook request failed after it began: {exc}",
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            ) from exc
        return _failed_request_result(rendered, started_at=started_at, error=exc)

    if status_code is None:
        raise RuntimeError("Webhook response status is unavailable")
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    if not 200 <= status_code < 400 and _delivery_redirect_chain_started.get() is True:
        raise WebhookAmbiguousResponseError(
            f"Redirect chain ended with HTTP {status_code}; the original request "
            "will not be retried automatically.",
            status_code=status_code,
            duration_ms=duration_ms,
        )
    return _completed_request_result(
        rendered,
        started_at=started_at,
        status_code=status_code,
        request_url=request_url,
        request_method=request_method,
        response_body_preview=response_body_preview,
    )


def _completed_request_result(
    rendered: RenderedNotificationRequestLike,
    *,
    started_at: float,
    status_code: int,
    request_url: str,
    request_method: str,
    response_body_preview: str | None,
) -> NotificationWebhookTestResponse:
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    success = 200 <= status_code < 400
    return NotificationWebhookTestResponse(
        success=success,
        status_code=status_code,
        duration_ms=duration_ms,
        rendered_url=request_url,
        rendered_method=request_method,
        rendered_headers=rendered.headers,
        rendered_query_params=rendered.query_params,
        rendered_body=rendered.body,
        response_body_preview=response_body_preview,
        error=None if success else f"HTTP {status_code}",
    )


def _preview_or_unavailable(
    response_body_preview: str | None,
    *,
    error: Exception,
) -> str:
    if response_body_preview is not None:
        return response_body_preview
    return f"Response preview unavailable ({type(error).__name__})."


def _observed_response_is_final(status_code: int | None) -> bool:
    if status_code is None:
        return False
    return (
        200 <= status_code < 400 or _delivery_redirect_chain_started.get() is not True
    )


def _failed_request_result(
    rendered: RenderedNotificationRequestLike,
    *,
    started_at: float,
    error: Exception,
) -> NotificationWebhookTestResponse:
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
        error=str(error),
    )


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
        try:
            client_timeout = getattr(client, "timeout", None)
            timeout = getattr(client_timeout, "read", None)
            _renew_notification_operation_lease(timeout)
            ensure_runtime_fetchable_url(
                current_url,
                allow_private_network=settings.allow_private_network_webhooks,
            )
            request_url = _merge_request_url(current_url, current_params)
            request = client.build_request(
                current_method,
                request_url,
                headers=headers,
                json=current_json_body,
                data=current_form_body
                if current_form_body is not None
                else current_raw_body,
            )
            _mark_notification_external_io_started()
            response = client.send(request, stream=True, follow_redirects=False)
        except (SafeFetchError, httpx.HTTPError, ValueError) as exc:
            if redirects > 0:
                raise RedirectError(
                    f"Redirect follow-up failed after the initial request: {exc}"
                ) from exc
            raise
        if response.status_code not in REDIRECT_STATUS_CODES:
            return response

        _mark_notification_redirect_chain_started()
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


def _renew_notification_operation_lease(timeout_seconds: float | int | None) -> None:
    callback = _delivery_lease_heartbeat.get()
    if callback is None:
        return
    timeout = max(1, int(timeout_seconds or 1))
    callback(max(30, (timeout * 2) + 15))


def _mark_notification_external_io_started() -> None:
    callback = _delivery_external_io_marker.get()
    if callback is not None:
        callback()
        _delivery_external_io_marker.set(None)
        _delivery_external_io_started.set(True)


def _mark_notification_redirect_chain_started() -> None:
    if _delivery_redirect_chain_started.get() is not None:
        _delivery_redirect_chain_started.set(True)


def _merge_request_url(url: str, params: list[tuple[str, str]]) -> str:
    if not params:
        return url

    split = urlsplit(url)
    query_pairs = parse_qsl(split.query, keep_blank_values=True)
    query_pairs.extend(params)
    merged_query = urlencode(query_pairs, doseq=True)
    return urlunsplit(
        (split.scheme, split.netloc, split.path, merged_query, split.fragment)
    )


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

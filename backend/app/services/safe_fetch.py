from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack
from typing import Any
from urllib.parse import urljoin

import httpcore
import httpx
from httpx._config import DEFAULT_LIMITS, Limits, create_ssl_context

from app.services.url_utils import ensure_runtime_fetchable_url, resolve_runtime_allowed_ips

REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
SAFE_FETCH_REQUEST_GUARD_EXTENSION = "threatlens_request_guard"


class SafeFetchError(RuntimeError):
    pass


class UnsafeTargetError(SafeFetchError):
    pass


class RedirectError(SafeFetchError):
    pass


class _GuardedSyncByteStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, guard_stack: ExitStack) -> None:
        self._stream = stream
        self._guard_stack = guard_stack
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield from self._stream

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        finally:
            self._guard_stack.close()


class _PinnedSyncBackend(httpcore.NetworkBackend):
    def __init__(self, *, allow_private_network: bool) -> None:
        self._allow_private_network = allow_private_network
        self._backend = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: httpcore.SOCKET_OPTION | list[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        candidates = resolve_runtime_allowed_ips(host, allow_private_network=self._allow_private_network)
        if not candidates:
            raise UnsafeTargetError("URL is not allowed for outbound fetch")

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                return self._backend.connect_tcp(
                    host=candidate,
                    port=port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # pragma: no cover - exercised via caller-visible failures
                last_error = exc

        assert last_error is not None
        raise last_error

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: httpcore.SOCKET_OPTION | list[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        return self._backend.connect_unix_socket(path=path, timeout=timeout, socket_options=socket_options)

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


class SafeHTTPTransport(httpx.HTTPTransport):
    def __init__(
        self,
        *,
        allow_private_network: bool,
        verify: bool = True,
        cert=None,
        trust_env: bool = True,
        http1: bool = True,
        http2: bool = False,
        limits: Limits = DEFAULT_LIMITS,
        local_address: str | None = None,
        retries: int = 0,
        socket_options=None,
    ) -> None:
        ssl_context = create_ssl_context(verify=verify, cert=cert, trust_env=trust_env)
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=http1,
            http2=http2,
            local_address=local_address,
            retries=retries,
            socket_options=socket_options,
            network_backend=_PinnedSyncBackend(allow_private_network=allow_private_network),
        )


def build_safe_http_client(
    *,
    timeout: httpx.Timeout,
    headers: dict[str, str] | None = None,
    allow_private_network: bool = False,
) -> httpx.Client:
    transport = SafeHTTPTransport(allow_private_network=allow_private_network)
    return httpx.Client(timeout=timeout, headers=headers, transport=transport)


def safe_get_with_redirects(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    allow_private_network: bool = False,
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
    allow_private_network: bool = False,
    max_redirects: int = 5,
    request_context: Callable[[str], AbstractContextManager[Any]] | None = None,
) -> httpx.Response:
    current_url = url
    redirects = 0
    current_method = method.upper()

    while True:
        _ensure_target(current_url, allow_private_network)
        guard_stack = ExitStack()
        request_guard = None
        if request_context is not None:
            request_guard = guard_stack.enter_context(request_context(current_url))
        try:
            request = client.build_request(current_method, current_url, headers=headers)
            response = client.send(request, stream=True, follow_redirects=False)
        except BaseException:
            guard_stack.close()
            raise
        if response.status_code not in REDIRECT_STATUS_CODES:
            if request_context is None:
                return response
            if response.is_closed:
                guard_stack.close()
                response.extensions[SAFE_FETCH_REQUEST_GUARD_EXTENSION] = None
                return response
            try:
                response.stream = _GuardedSyncByteStream(response.stream, guard_stack)
                response.extensions[SAFE_FETCH_REQUEST_GUARD_EXTENSION] = request_guard
            except BaseException:
                try:
                    response.close()
                finally:
                    guard_stack.close()
                raise
            return response

        try:
            location = response.headers.get("location")
            response.close()
        finally:
            guard_stack.close()
        if not location:
            raise RedirectError("Redirect missing location header")

        redirects += 1
        if redirects > max_redirects:
            raise RedirectError("Too many redirects")

        current_url = urljoin(current_url, location)
        if response.status_code == 303 and current_method != "HEAD":
            current_method = "GET"


def safe_fetch_request_guard(response: httpx.Response) -> object | None:
    extensions = getattr(response, "extensions", None)
    if not isinstance(extensions, dict):
        return None
    return extensions.get(SAFE_FETCH_REQUEST_GUARD_EXTENSION)


def _ensure_target(url: str, allow_private_network: bool) -> None:
    try:
        ensure_runtime_fetchable_url(url, allow_private_network=allow_private_network)
    except ValueError as exc:
        raise UnsafeTargetError("URL is not allowed for outbound fetch") from exc

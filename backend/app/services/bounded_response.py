"""Read bounded decoded responses without HTTPX's unbounded decoder chunks."""
from __future__ import annotations

import zlib
from collections.abc import Callable

import httpx

from app.services.outbound_deadline import check_outbound_deadline


class ResponseBodyTooLarge(ValueError):
    pass


def read_bounded_response(
    response: httpx.Response,
    max_bytes: int,
    *,
    check: Callable[[], None] | None = None,
    too_large_error: type[Exception] = ResponseBodyTooLarge,
) -> bytes:
    """Cap both encoded input and decoded output, including error responses.

    Advertise gzip/deflate/identity at the client; reject other or stacked
    encodings. Each zlib operation has an explicit output allocation limit.
    """
    return _read_response(
        response, max_bytes, check=check, too_large_error=too_large_error,
        truncate=False,
    )


def read_response_prefix(
    response: httpx.Response,
    max_bytes: int,
    *,
    check: Callable[[], None] | None = None,
) -> bytes:
    """Read at most a decoded prefix without allocating the rest of the body.

    Stop as soon as the prefix is complete; the caller must close the response.
    Encoded input is streamed under the request deadline, rather than capped at
    the prefix length (compressed input can be larger than its decoded prefix).
    """
    return _read_response(
        response, max_bytes, check=check, too_large_error=ResponseBodyTooLarge,
        truncate=True,
    )


def _read_response(
    response: httpx.Response,
    max_bytes: int,
    *,
    check: Callable[[], None] | None,
    too_large_error: type[Exception],
    truncate: bool,
) -> bytes:
    if max_bytes < 0:
        raise ValueError("response byte limit must be non-negative")
    body = bytearray()
    encoded_size = 0

    def guard(*, check_ownership: bool = True) -> None:
        check_outbound_deadline()
        if check_ownership and check is not None:
            check()

    def append(chunk: bytes) -> None:
        guard(check_ownership=not truncate)
        remaining = max_bytes - len(body)
        if len(chunk) > remaining:
            if not truncate:
                raise too_large_error("response body exceeds configured cap")
            chunk = chunk[:remaining]
        body.extend(chunk)

    # Prefix consumers renew their operation lease once per received chunk,
    # avoiding repeated database heartbeats for one decompression operation.
    guard(check_ownership=not truncate)
    if truncate and max_bytes == 0:
        return b""
    # Preloaded responses occur in tests and custom transports. HTTPX has
    # already decoded them; never try to decode their Content-Encoding twice.
    if getattr(response, "is_stream_consumed", False):
        if truncate:
            guard()
        append(response.content)
        return bytes(body)
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"", "identity", "gzip", "deflate"}:
        raise httpx.DecodingError("unsupported response content encoding")
    decoder = zlib.decompressobj(31 if encoding == "gzip" else 15) if encoding in {"gzip", "deflate"} else None
    try:
        for chunk in response.iter_raw():
            guard()
            encoded_size += len(chunk)
            if not truncate and encoded_size > max_bytes:
                raise too_large_error("encoded response body exceeds configured cap")
            if decoder is None:
                append(chunk)
            else:
                # A max_length of zero means unlimited to zlib, hence +1.
                append(decoder.decompress(chunk, max_bytes - len(body) + 1))
                if truncate and len(body) == max_bytes:
                    return bytes(body)
                if decoder.unconsumed_tail:
                    raise too_large_error("response body exceeds configured cap")
                if decoder.unused_data:
                    raise httpx.DecodingError("trailing compressed response data")
            if truncate and len(body) == max_bytes:
                return bytes(body)
        # HTTPX closes the stream (and its domain guard) on EOF. Ownership
        # checks apply while reading; only the deadline remains relevant here.
        check_outbound_deadline()
        if decoder is not None and not decoder.eof:
            raise httpx.DecodingError("incomplete compressed response body")
    except zlib.error as exc:
        raise httpx.DecodingError("invalid compressed response body") from exc
    return bytes(body)

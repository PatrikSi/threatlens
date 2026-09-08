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
    body = bytearray()
    encoded_size = 0

    def guard() -> None:
        check_outbound_deadline()
        if check is not None:
            check()

    def append(chunk: bytes) -> None:
        guard()
        if len(chunk) > max_bytes - len(body):
            raise too_large_error("response body exceeds configured cap")
        body.extend(chunk)

    guard()
    # Preloaded responses occur in tests and custom transports. HTTPX has
    # already decoded them; never try to decode their Content-Encoding twice.
    if getattr(response, "is_stream_consumed", False):
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
            if encoded_size > max_bytes:
                raise too_large_error("encoded response body exceeds configured cap")
            if decoder is None:
                append(chunk)
            else:
                # A max_length of zero means unlimited to zlib, hence +1.
                append(decoder.decompress(chunk, max_bytes - len(body) + 1))
                if decoder.unconsumed_tail:
                    raise too_large_error("response body exceeds configured cap")
                if decoder.unused_data:
                    raise httpx.DecodingError("trailing compressed response data")
        # HTTPX closes the stream (and its domain guard) on EOF. Ownership
        # checks apply while reading; only the deadline remains relevant here.
        check_outbound_deadline()
        if decoder is not None and not decoder.eof:
            raise httpx.DecodingError("incomplete compressed response body")
    except zlib.error as exc:
        raise httpx.DecodingError("invalid compressed response body") from exc
    return bytes(body)

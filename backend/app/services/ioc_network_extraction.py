"""Bounded token parsing for literal and defanged network observations."""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_left, insort
import ipaddress
import re
from collections.abc import Iterator
from urllib.parse import urlsplit

from app.services.ioc_normalization import (
    MAX_NETWORK_TOKEN_CHARS, normalize_domain, normalize_email, normalize_url, refang,
)


_TOKEN = re.compile(r"[^\s<>\"`|]+")
_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9!#$%&'*+/=?^_`{|}~.-])"
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}@[\w.-]{1,253}(?![\w.-])"
)
_HOST = re.compile(r"[\w.-]+", re.UNICODE)
_IPV6 = re.compile(r"(?<![\w:.%])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:.%])")


@dataclass(frozen=True)
class NetworkMatch:
    type: str
    value_norm: str
    start: int
    end: int
    transformations: tuple[str, ...]


def _trim_url_end(value: str) -> int:
    end = len(value.rstrip(".,;!?"))
    for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
        unmatched = value[:end].count(closing) - value[:end].count(opening)
        while end and value[end - 1] == closing and unmatched > 0:
            end -= 1
            unmatched -= 1
    return end


def network_matches(text: str) -> Iterator[NetworkMatch]:
    for token in _TOKEN.finditer(text):
        raw = token.group()
        if len(raw) > MAX_NETWORK_TOKEN_CHARS or not any(char in raw for char in ".:@"):
            continue
        yield from _token_matches(raw, offset=token.start())


def _token_matches(raw: str, *, offset: int) -> Iterator[NetworkMatch]:
    normalized = refang(raw)
    value = normalized.text
    occupied: list[tuple[int, int]] = []

    def emit(kind: str, norm: str, start: int, end: int) -> NetworkMatch:
        left, right = normalized.original_span(start, end)
        return NetworkMatch(kind, norm, offset + left, offset + right,
                            normalized.transformations(start, end))

    for match in _URL.finditer(value):
        candidate = match.group()
        end = match.start() + _trim_url_end(candidate)
        candidate = value[match.start():end]
        # An invalid URL must not donate misleading host/path observations.
        occupied.append(match.span())
        norm = normalize_url(candidate)
        if norm is None:
            continue
        yield emit("url", norm, match.start(), end)
        parsed = urlsplit(candidate)
        # URLSplitResult.hostname lowercases Unicode and can change its
        # length. Derive evidence boundaries from the original authority.
        authority = parsed.netloc
        host = authority[1:authority.index("]")] if authority.startswith("[") else authority.split(":", 1)[0]
        host_start = match.start() + candidate.index("://") + 3
        if candidate[host_start - match.start():].startswith("["):
            host_start += 1
        host_end = host_start + len(host)
        try:
            address = ipaddress.ip_address(host)
            kind, host_norm = ("ipv4" if address.version == 4 else "ipv6"), address.compressed
        except ValueError:
            kind, host_norm = "domain", normalize_domain(host)
        if host_norm:
            yield emit(kind, host_norm, host_start, host_end)

    def available(start: int, end: int) -> bool:
        index = bisect_left(occupied, (start, -1))
        return ((index == 0 or occupied[index - 1][1] <= start)
                and (index == len(occupied) or occupied[index][0] >= end))

    for match in _EMAIL.finditer(value):
        if not available(*match.span()):
            continue
        end = match.end()
        while end > match.start() and value[end - 1] == ".":
            end -= 1
        norm = normalize_email(value[match.start():end])
        # Reserve even invalid email syntax rather than mislabeling its domain.
        insort(occupied, match.span())
        if norm:
            yield emit("email", norm, match.start(), end)

    for match in _IPV6.finditer(value):
        if not available(*match.span()):
            continue
        try:
            address = ipaddress.IPv6Address(match.group())
        except ValueError:
            continue
        if str(address) != "::":
            insort(occupied, match.span())
            yield emit("ipv6", address.compressed, *match.span())

    if "@" in value:
        # Even an unsupported/malformed mailbox must not promote its domain
        # or dotted local part to independent infrastructure evidence.
        return
    for match in _HOST.finditer(value):
        if not available(*match.span()):
            continue
        candidate = match.group().rstrip(".")
        if "." not in candidate or not candidate:
            continue
        try:
            address = ipaddress.IPv4Address(candidate)
            kind, norm = "ipv4", str(address)
        except ValueError:
            kind, norm = "domain", normalize_domain(candidate)
        if norm:
            yield emit(kind, norm, match.start(), match.start() + len(candidate))

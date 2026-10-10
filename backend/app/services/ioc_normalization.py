"""Pure indicator normalization; extracted destinations are never requested."""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_left, bisect_right
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


MAX_NETWORK_TOKEN_CHARS = 4096
_DEFANG = re.compile(r"hxxps?(?=[:\[(])|\[\.\]|\(\.\)|\[@\]|\(@\)|\[:\]|\(:\)", re.IGNORECASE)
_LOCAL_PART = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}")


@dataclass(frozen=True)
class RefangChange:
    start: int
    end: int
    original_start: int
    original_end: int
    kind: str


@dataclass(frozen=True)
class RefangedText:
    text: str
    starts: tuple[int, ...] = ()
    ends: tuple[int, ...] = ()
    changes: tuple[RefangChange, ...] = ()

    def _original_index(self, index: int, *, end: bool) -> int:
        change_index = bisect_right(self.starts, index) - 1
        if change_index < 0:
            return index + int(end)
        change = self.changes[change_index]
        if index < change.end:
            return change.original_end if end else change.original_start
        return index + change.original_end - change.end + int(end)

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        return self._original_index(start, end=False), self._original_index(end - 1, end=True)

    def transformations(self, start: int, end: int) -> tuple[str, ...]:
        left = bisect_right(self.ends, start)
        right = bisect_left(self.starts, end)
        return tuple(dict.fromkeys(
            change.kind for change in self.changes[left:right]
        ))


def refang(value: str) -> RefangedText:
    """Use sparse edit boundaries to map normalized matches to original spans.

    Unchanged tokens retain no mapping allocations. Callers bound token size;
    lookup is logarithmic in edits rather than scanning every edit per match.
    """
    chunks: list[str] = []
    changes: list[RefangChange] = []
    cursor = 0
    output_length = 0
    for match in _DEFANG.finditer(value):
        chunks.append(value[cursor:match.start()])
        output_length += match.start() - cursor
        raw = match.group().lower()
        if raw.startswith("hxxp"):
            replacement, kind = raw.replace("hxxp", "http"), "refang_scheme"
        else:
            replacement = raw[1]
            kind = {".": "refang_dot", "@": "refang_at", ":": "refang_colon"}[replacement]
        changes.append(RefangChange(output_length, output_length + len(replacement),
                                   match.start(), match.end(), kind))
        chunks.append(replacement)
        output_length += len(replacement)
        cursor = match.end()
    if not changes:
        return RefangedText(value)
    chunks.append(value[cursor:])
    return RefangedText("".join(chunks), tuple(change.start for change in changes),
                        tuple(change.end for change in changes), tuple(changes))


def normalize_domain(value: str, *, strip_www: bool = True) -> str | None:
    candidate = value.rstrip(".")
    if len(candidate) > 253 or "." not in candidate or any(ord(char) < 33 for char in candidate):
        return None
    try:
        candidate = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if len(candidate) > 253:
        return None
    labels = candidate.split(".")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        return None
    if not re.fullmatch(r"(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})", labels[-1]):
        return None
    return candidate[4:] if strip_www and candidate.startswith("www.") else candidate


def normalize_email(value: str) -> str | None:
    if len(value) > 320 or value.count("@") != 1:
        return None
    local, domain = value.rsplit("@", 1)
    if not _LOCAL_PART.fullmatch(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        return None
    host = normalize_domain(domain, strip_www=False)
    # Local-part case can be significant. Do not merge different mailboxes.
    return f"{local}@{host}" if host else None


def normalize_url(value: str) -> str | None:
    if len(value) > MAX_NETWORK_TOKEN_CHARS or any(ord(char) < 33 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None or "%" in parsed.hostname:
            return None
        port = parsed.port
        try:
            address = ipaddress.ip_address(parsed.hostname)
            host = f"[{address.compressed}]" if address.version == 6 else str(address)
        except ValueError:
            host = normalize_domain(parsed.hostname, strip_www=False)
        if host is None:
            return None
        if port is not None and port != (443 if parsed.scheme.lower() == "https" else 80):
            host += f":{port}"
        return urlunsplit((parsed.scheme.lower(), host, parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return None

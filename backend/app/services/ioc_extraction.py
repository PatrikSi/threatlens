from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
import ipaddress
import re

from app.services.ioc_network_extraction import network_matches
from app.services.ioc_normalization import normalize_domain, normalize_email, normalize_url, refang


MAX_SOURCE_CHARS = 4_000_000
MAX_EXTRACTED_OCCURRENCES = 100_000
EVIDENCE_CONTEXT_CHARS = 120


class IOCExtractionLimitError(ValueError):
    """The source exceeds a documented extraction capacity, without partial output."""


HASH_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
HASH_SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
HASH_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)


VENDOR_TERMS = (
    "microsoft",
    "google",
    "apple",
    "aws",
    "azure",
    "oracle",
    "sap",
    "vmware",
    "cisco",
    "fortinet",
    "juniper",
    "palo alto networks",
    "crowdstrike",
    "ivanti",
    "atlassian",
    "citrix",
    "mitel",
    "okta",
    "linux foundation",
    "mozilla",
)

PROGRAM_TERMS = (
    "active directory",
    "windows",
    "windows server",
    "microsoft exchange",
    "sharepoint",
    "office 365",
    "defender",
    "fortios",
    "pan-os",
    "vmware esxi",
    "vcenter",
    "openssh",
    "openssl",
    "docker",
    "kubernetes",
    "gitlab",
    "jenkins",
    "confluence",
    "jira",
    "wordpress",
)

PROGRAM_PATTERNS = tuple(
    (term, re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.IGNORECASE | re.ASCII))
    for term in sorted(PROGRAM_TERMS, key=len, reverse=True)
)
VENDOR_PATTERNS = tuple(
    (term, re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", re.IGNORECASE | re.ASCII))
    for term in sorted(VENDOR_TERMS, key=len, reverse=True)
)


@dataclass(frozen=True)
class ExtractedIOC:
    type: str
    value_raw: str
    value_norm: str
    source_section: str
    confidence: float
    source_start: int | None = None
    source_end: int | None = None
    evidence_text: str | None = None
    transformations: tuple[str, ...] = ()


def normalize_ioc_search_value(value: str) -> tuple[str, str] | None:
    """Recognize a complete IOC value using the extraction normalizers."""

    candidate = value.strip()
    if not candidate or len(candidate) > 4096:
        return None
    candidate = refang(candidate).text
    if normalized_url := normalize_url(candidate):
        return "url", normalized_url
    if normalized_email := normalize_email(candidate):
        return "email", normalized_email
    if HASH_SHA256_RE.fullmatch(candidate):
        return "hash_sha256", candidate.lower()
    if HASH_SHA1_RE.fullmatch(candidate):
        return "hash_sha1", candidate.lower()
    if HASH_MD5_RE.fullmatch(candidate):
        return "hash_md5", candidate.lower()
    if CVE_RE.fullmatch(candidate):
        return "cve", candidate.upper()
    normalized_ip = _normalize_ipv4(candidate)
    if normalized_ip is not None:
        return "ipv4", normalized_ip
    normalized_ip = _normalize_ipv6(candidate)
    if normalized_ip is not None:
        return "ipv6", normalized_ip
    if normalized_domain := normalize_domain(candidate):
        return "domain", normalized_domain
    lowered = candidate.lower()
    if lowered in VENDOR_TERMS:
        return "vendor", lowered
    if lowered in PROGRAM_TERMS:
        return "program", lowered
    return None


def extract_iocs(*, title: str, summary: str | None, article_text: str | None) -> list[ExtractedIOC]:
    sections: tuple[tuple[str, str | None], ...] = (
        ("title", title),
        ("summary", summary),
        ("article", article_text),
    )

    matches: list[ExtractedIOC] = []
    for section_name, value in sections:
        if not value:
            continue
        if len(value) > MAX_SOURCE_CHARS:
            raise IOCExtractionLimitError("IOC source exceeds the 4,000,000-character extraction limit")
        for match in _extract_from_text(value, section_name):
            if len(matches) >= MAX_EXTRACTED_OCCURRENCES:
                raise IOCExtractionLimitError("IOC source exceeds the 100,000-occurrence extraction limit")
            matches.append(match)
    return matches


def _extract_from_text(text: str, section: str) -> Iterator[ExtractedIOC]:
    def occurrence(kind: str, norm: str, start: int, end: int, confidence: float,
                   transformations: tuple[str, ...] = ()) -> ExtractedIOC:
        return ExtractedIOC(
            type=kind, value_raw=text[start:end], value_norm=norm,
            source_section=section, confidence=confidence, source_start=start, source_end=end,
            evidence_text=text[max(0, start - EVIDENCE_CONTEXT_CHARS):min(len(text), end + EVIDENCE_CONTEXT_CHARS)],
            transformations=transformations,
        )

    # Whole-word hexadecimal runs of different lengths cannot overlap. Each
    # pattern scans the source once; no pairwise span comparisons are needed.
    for kind, pattern in (("hash_sha256", HASH_SHA256_RE), ("hash_sha1", HASH_SHA1_RE),
                          ("hash_md5", HASH_MD5_RE), ("cve", CVE_RE)):
        for match in pattern.finditer(text):
            norm = match.group().upper() if kind == "cve" else match.group().lower()
            yield occurrence(kind, norm, *match.span(), 1.0)

    for match in network_matches(text):
        yield occurrence(match.type, match.value_norm, match.start, match.end,
                         0.95 if match.type in {"domain", "url", "email"} else 1.0,
                         match.transformations)

    # ASCII case-insensitive matching preserves source offsets for Unicode text.
    # Lowercasing the whole input can expand characters and invalidate evidence.
    for kind, patterns in (("vendor", VENDOR_PATTERNS), ("program", PROGRAM_PATTERNS)):
        for term, pattern in patterns:
            for match in pattern.finditer(text):
                yield occurrence(kind, term, *match.span(), 0.7)


def _normalize_ipv4(value: str) -> str | None:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return None
    if not isinstance(parsed, ipaddress.IPv4Address):
        return None
    return str(parsed)


def _normalize_ipv6(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if "%" in candidate:
        return None
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if not isinstance(parsed, ipaddress.IPv6Address):
        return None
    return parsed.compressed

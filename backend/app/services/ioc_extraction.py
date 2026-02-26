from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re


HASH_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
HASH_SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
HASH_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)\.)+[A-Za-z]{2,24}\b")


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
    (term, re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"))
    for term in sorted(PROGRAM_TERMS, key=len, reverse=True)
)
VENDOR_PATTERNS = tuple(
    (term, re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"))
    for term in sorted(VENDOR_TERMS, key=len, reverse=True)
)


@dataclass(frozen=True)
class ExtractedIOC:
    type: str
    value_raw: str
    value_norm: str
    source_section: str
    confidence: float


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
        matches.extend(_extract_from_text(value, section_name))
    return matches


def _extract_from_text(text: str, section: str) -> list[ExtractedIOC]:
    lowered = text.lower()
    occupied_spans: list[tuple[int, int]] = []
    matches: list[ExtractedIOC] = []

    for match in HASH_SHA256_RE.finditer(text):
        if _is_overlapping(match.start(), match.end(), occupied_spans):
            continue
        occupied_spans.append((match.start(), match.end()))
        raw = match.group(0)
        matches.append(ExtractedIOC(type="hash_sha256", value_raw=raw, value_norm=raw.lower(), source_section=section, confidence=1.0))

    for match in HASH_SHA1_RE.finditer(text):
        if _is_overlapping(match.start(), match.end(), occupied_spans):
            continue
        occupied_spans.append((match.start(), match.end()))
        raw = match.group(0)
        matches.append(ExtractedIOC(type="hash_sha1", value_raw=raw, value_norm=raw.lower(), source_section=section, confidence=1.0))

    for match in HASH_MD5_RE.finditer(text):
        if _is_overlapping(match.start(), match.end(), occupied_spans):
            continue
        occupied_spans.append((match.start(), match.end()))
        raw = match.group(0)
        matches.append(ExtractedIOC(type="hash_md5", value_raw=raw, value_norm=raw.lower(), source_section=section, confidence=1.0))

    for match in CVE_RE.finditer(text):
        raw = match.group(0)
        matches.append(ExtractedIOC(type="cve", value_raw=raw, value_norm=raw.upper(), source_section=section, confidence=1.0))

    for match in IPV4_RE.finditer(text):
        raw = match.group(0)
        parsed = _normalize_ipv4(raw)
        if parsed:
            matches.append(ExtractedIOC(type="ipv4", value_raw=raw, value_norm=parsed, source_section=section, confidence=1.0))

    for match in DOMAIN_RE.finditer(text):
        raw = match.group(0)
        if "@" in raw:
            continue
        normalized = raw.strip(". ").lower()
        if normalized.startswith("www."):
            normalized = normalized[4:]
        if normalized and "." in normalized:
            matches.append(
                ExtractedIOC(type="domain", value_raw=raw, value_norm=normalized, source_section=section, confidence=0.95)
            )

    for term, pattern in VENDOR_PATTERNS:
        for match in pattern.finditer(lowered):
            raw = text[match.start() : match.end()]
            matches.append(ExtractedIOC(type="vendor", value_raw=raw, value_norm=term, source_section=section, confidence=0.7))

    for term, pattern in PROGRAM_PATTERNS:
        for match in pattern.finditer(lowered):
            raw = text[match.start() : match.end()]
            matches.append(ExtractedIOC(type="program", value_raw=raw, value_norm=term, source_section=section, confidence=0.7))

    return matches


def _normalize_ipv4(value: str) -> str | None:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return None
    if not isinstance(parsed, ipaddress.IPv4Address):
        return None
    return str(parsed)


def _is_overlapping(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for left, right in spans:
        if start < right and end > left:
            return True
    return False

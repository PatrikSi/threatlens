"""Structural grounding checks; these do not prove that a claim follows from a quote."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from markdown_it import MarkdownIt


CITATION_PATTERN = re.compile(r"\[(S\d+)\]")
SOURCE_HEADER = re.compile(r"^\[(S\d+)\]\s")
NO_FINDINGS_BODY = "No supported findings were identified in the supplied evidence."
_MARKDOWN = MarkdownIt("commonmark", {"html": True, "maxNesting": 32}).enable(["table", "strikethrough"])


class ReportGroundingError(ValueError):
    """A received response cannot safely be published as grounded report content."""

    code = "invalid_provider_output"


@dataclass(frozen=True)
class GroundedSection:
    body: str
    key_points: list[str]
    citations: list[str]
    claim_blocks: int


def report_stage_input(messages: list[dict[str, str]]) -> dict | None:
    """Recognize envelopes produced by the report planner, not arbitrary JSON calls."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        try:
            value = json.loads(message.get("content", ""))
        except (TypeError, ValueError):
            return None
        if isinstance(value, dict) and (
            "evidence" in value or ("section" in value and "findings" in value)
        ):
            return value
    return None


def evidence_sources(stage: dict) -> dict[str, str]:
    sources: dict[str, str] = {}
    for entry in stage.get("evidence", []):
        if isinstance(entry, str) and (match := SOURCE_HEADER.match(entry)):
            sources[match[1]] = entry
    return sources


def validate_findings(value: object, *, sources: dict[str, str]) -> list[dict]:
    if not isinstance(value, list) or len(value) > 100:
        raise ReportGroundingError("Report evidence must contain a findings array with at most 100 entries.")
    normalized: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict) or not _text(entry.get("text"), maximum=8000):
            raise ReportGroundingError("Each report finding must contain nonempty text.")
        citations = _citations(entry.get("citations"), known=set(sources))
        _reject_unknown_markers(entry["text"], set(citations))
        quotes = entry.get("evidence_quotes")
        if not isinstance(quotes, list) or not 1 <= len(quotes) <= 100:
            raise ReportGroundingError("Each report finding must include exact evidence_quotes for its citations.")
        quoted: set[str] = set()
        checked_quotes: list[dict[str, str]] = []
        for quote in quotes:
            if not isinstance(quote, dict):
                raise ReportGroundingError("Report evidence quotes must be objects containing citation and quote.")
            citation, excerpt = quote.get("citation"), quote.get("quote")
            if not isinstance(citation, str) or citation not in citations:
                raise ReportGroundingError("A report evidence quote refers to an unavailable citation.")
            if not _text(excerpt, maximum=2000) or len(_compact(excerpt)) < 12:
                raise ReportGroundingError("Report evidence quotes must contain 12–2,000 characters of source text.")
            if _compact(excerpt) not in _compact(sources[citation]):
                raise ReportGroundingError(f"The evidence quote for {citation} does not occur in the supplied excerpt.")
            quoted.add(citation)
            checked_quotes.append({"citation": citation, "quote": excerpt.strip()})
        if quoted != set(citations):
            raise ReportGroundingError("Every finding citation needs a matching exact evidence quote.")
        normalized.append({"text": entry["text"].strip(), "citations": citations, "evidence_quotes": checked_quotes})
    return normalized


def validate_section(payload: dict, *, known_citations: set[str]) -> GroundedSection:
    body = payload.get("body_markdown")
    if not _text(body, maximum=400_000):
        raise ReportGroundingError("The report section must contain nonempty body_markdown text.")
    citations = _citations(payload.get("citations"), known=known_citations)
    _reject_unknown_markers(body, known_citations)
    used, claim_blocks = _claim_citations(body, known_citations)
    if claim_blocks == 0 or not used:
        raise ReportGroundingError("The report section contains no cited narrative claims.")
    if set(citations) != used:
        raise ReportGroundingError("Report section citations must match the citations used in its narrative.")
    key_points = payload.get("key_points", [])
    if not isinstance(key_points, list) or len(key_points) > 12:
        raise ReportGroundingError("Report key_points must be an array with at most 12 entries.")
    for point in key_points:
        if not _text(point, maximum=4000):
            raise ReportGroundingError("Report key points must contain nonempty text.")
        _reject_unknown_markers(point, used)
        point_citations, blocks = _claim_citations(point, used)
        if not point_citations or not blocks:
            raise ReportGroundingError("Each report key point must cite its supporting source.")
    return GroundedSection(body.strip(), [point.strip() for point in key_points], citations, claim_blocks)


def validate_stage_output(payload: dict, *, stage: dict) -> None:
    if "evidence" in stage:
        validate_findings(payload.get("findings"), sources=evidence_sources(stage))
    else:
        known = {
            citation for finding in stage.get("findings", []) if isinstance(finding, dict)
            for citation in finding.get("citations", []) if isinstance(citation, str)
        }
        validate_section(payload, known_citations=known)


def _claim_citations(body: str, known: set[str]) -> tuple[set[str], int]:
    used: set[str] = set()
    count = 0
    stack: list[str] = []
    row_text: list[str] = []

    def check(content: str) -> None:
        nonlocal count
        if not any(character.isalpha() for character in CITATION_PATTERN.sub("", content)):
            return
        citations = set(CITATION_PATTERN.findall(content)) & known
        if not citations:
            raise ReportGroundingError("Every narrative paragraph, list item and textual table row must carry a valid source citation.")
        count += 1
        used.update(citations)

    for token in _MARKDOWN.parse(body):
        if token.type == "inline":
            for child in token.children or []:
                if child.type in {"text", "code_inline"}:
                    _reject_unknown_markers(child.content, known)
        if token.nesting == 1:
            stack.append(token.type)
        elif token.nesting == -1:
            if token.type == "tr_close" and "thead_open" not in stack:
                check(" ".join(row_text))
                row_text = []
            stack.pop()
        elif token.type == "inline" and not any(t in stack for t in ("heading_open", "thead_open")):
            link_depth = 0
            text: list[str] = []
            for child in token.children or []:
                if child.type == "link_open":
                    link_depth += 1
                elif child.type == "link_close":
                    link_depth -= 1
                elif child.type == "code_inline":
                    # Inline code is visible content, but its literal markers
                    # are not navigable source citations in any renderer.
                    text.append(CITATION_PATTERN.sub("", child.content))
                elif child.type == "text":
                    # Link labels are visible claims, but their embedded markers
                    # do not become source links in the report renderer.
                    text.append(CITATION_PATTERN.sub("", child.content) if link_depth else child.content)
            content = " ".join(text).strip()
            if "tr_open" in stack:
                row_text.append(content)
            else:
                check(content)
    return used, count


def _citations(value: object, *, known: set[str]) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ReportGroundingError("Report citations must be a nonempty array of supplied source identifiers.")
    if any(not isinstance(citation, str) or citation not in known for citation in value):
        raise ReportGroundingError("Report output cites a source that was not supplied to this generation stage.")
    return list(dict.fromkeys(value))


def _reject_unknown_markers(value: str, known: set[str]) -> None:
    if set(CITATION_PATTERN.findall(value)) - known:
        raise ReportGroundingError("Report output contains an unsupported inline citation; its claim was not published.")


def _text(value: object, *, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= maximum


def _compact(value: str) -> str:
    return " ".join(value.split())

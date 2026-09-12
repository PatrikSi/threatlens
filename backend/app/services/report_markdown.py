"""Bounded Markdown structure shared by the offline report export renderers.

HTML tokens are recognized only to discard them. Neither renderer uses the
parser's HTML renderer or interprets user text as ReportLab markup.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from app.schemas.reports import ReportDetailResponse, ReportSourceResponse

MAX_RENDER_BYTES = 4 * 1024 * 1024
MAX_RENDER_NODES = 100_000
MAX_SECTION_BYTES = 256 * 1024
MAX_PDF_PAGES = 200
CITATION_PATTERN = re.compile(r"\[(S\d+)\]")


class ReportRenderingLimitError(ValueError):
    """A structured export exceeds the renderer's finite resource budget."""


@dataclass(frozen=True)
class ReportMarkdown:
    sections: list[SyntaxTreeNode]
    sources: dict[str, ReportSourceResponse]


def parse_report(report: ReportDetailResponse) -> ReportMarkdown:
    values = [report.title, report.model or ""]
    values.extend(str(warning) for warning in report.coverage.get("warnings") or [])
    for section in report.sections:
        body = section.body_markdown or ""
        if len(body) > MAX_SECTION_BYTES or len(body.encode("utf-8")) > MAX_SECTION_BYTES:
            raise ReportRenderingLimitError("A report section exceeds the structured export text limit.")
        values.extend((section.title, body))
    for source in report.sources:
        values.extend((source.citation_key, source.title, source.feed_name, source.url))
    remaining = MAX_RENDER_BYTES
    node_count = len(values) + sum(value.count("\n") for value in values)
    if node_count > MAX_RENDER_NODES:
        raise ReportRenderingLimitError("Report structure exceeds the structured export limit.")
    for value in values:
        # Check characters first so a single giant value cannot cause a giant encode.
        if len(value) > remaining:
            raise ReportRenderingLimitError("Report text exceeds the structured export limit.")
        remaining -= len(value.encode("utf-8"))
        if remaining < 0:
            raise ReportRenderingLimitError("Report text exceeds the structured export limit.")
    parser = MarkdownIt("commonmark", {"html": True, "maxNesting": 32}).enable(["table", "strikethrough"])
    sections = []
    for section in report.sections:
        tree = SyntaxTreeNode(parser.parse(section.body_markdown or "_No content generated._"))
        node_count += sum(1 + node.content.count("\n") if node.type in {"fence", "code_block"} else 1 for node in tree.walk())
        if node_count > MAX_RENDER_NODES:
            raise ReportRenderingLimitError("Report structure exceeds the structured export limit.")
        sections.append(tree)
    sources = {
        source.citation_key: source for source in report.sources
        if source.included and re.fullmatch(r"S\d+", source.citation_key)
    }
    return ReportMarkdown(sections=sections, sources=sources)


def safe_external_url(value: str) -> str | None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def source_anchor(citation: str) -> str:
    return f"report-source-{citation}"


def text_markup(value: str, sources: dict, *, pdf: bool = False, citations: bool = True) -> str:
    if not citations:
        return escape(value)
    tag = "link" if pdf else "a"
    output = []
    start = 0
    for match in CITATION_PATTERN.finditer(value):
        output.append(escape(value[start:match.start()]))
        key = match.group(1)
        label = match.group(0)
        if key in sources:
            if pdf:
                output.append(f'<link href="#{source_anchor(key)}" color="#0f766e"><u>{label}</u></link>')
            else:
                output.append(f'<{tag} href="#{source_anchor(key)}">{label}</{tag}>')
        else:
            output.append(label)
        start = match.end()
    output.append(escape(value[start:]))
    return "".join(output)


def heading_offset(tree: SyntaxTreeNode) -> int:
    levels = [int(node.tag[1:]) for node in tree.walk() if node.type == "heading"]
    return 3 - min(levels) if levels else 0


def inline_markup(node: SyntaxTreeNode, sources: dict, *, pdf: bool = False, citations: bool = True) -> str:
    kind = node.type
    if kind == "text":
        return text_markup(node.content, sources, pdf=pdf, citations=citations)
    if kind in {"html_inline", "html_block"}:
        return ""
    if kind == "code_inline":
        tag = 'font name="ThreatLensMono"' if pdf else "code"
        close = "font" if pdf else "code"
        return f"<{tag}>{escape(node.content)}</{close}>"
    if kind in {"softbreak", "hardbreak"}:
        return "<br/>" if kind == "hardbreak" else "\n"
    if kind == "image":
        return f"[Image omitted: {escape(node.content or 'image')}]"
    inner = "".join(inline_markup(child, sources, pdf=pdf, citations=citations and kind != "link") for child in node.children or [])
    if kind in {"strong", "em", "s"}:
        tag = {"strong": "b", "em": "i", "s": "strike"}[kind] if pdf else {"s": "del"}.get(kind, kind)
        return f"<{tag}>{inner}</{tag}>"
    if kind == "link":
        href = safe_external_url(str(node.attrGet("href") or ""))
        if not href:
            return inner
        tag = "link" if pdf else "a"
        if pdf:
            return f'<link href="{escape(href, quote=True)}" color="#0f766e"><u>{inner}</u></link>'
        return f'<{tag} href="{escape(href, quote=True)}">{inner}</{tag}>' 
    return inner


def html_fragment(tree: SyntaxTreeNode, sources: dict) -> str:
    offset = heading_offset(tree)

    def block(node: SyntaxTreeNode) -> str:
        kind = node.type
        if kind in {"html_inline", "html_block"}:
            return ""
        if kind == "inline":
            return inline_markup(node, sources)
        if kind in {"fence", "code_block"}:
            return f"<pre><code>{escape(node.content)}</code></pre>"
        if kind == "hr":
            return "<hr>"
        inner = "".join(block(child) for child in node.children or [])
        if kind == "root":
            return inner
        if kind == "heading":
            tag = f"h{min(6, int(node.tag[1:]) + offset)}"
        else:
            tag = {"paragraph": "p", "bullet_list": "ul", "ordered_list": "ol", "list_item": "li",
                   "blockquote": "blockquote", "table": "table", "thead": "thead", "tbody": "tbody",
                   "tr": "tr", "th": "th", "td": "td"}.get(kind)
        if not tag:
            return inner
        attr = ' scope="col"' if tag == "th" else ""
        if tag == "ol":
            attr = f' start="{int(node.attrGet("start") or 1)}"'
        result = f"<{tag}{attr}>{inner}</{tag}>"
        return f'<div class="table-scroll">{result}</div>' if tag == "table" else result

    return block(tree)


def coverage_notes(report: ReportDetailResponse) -> list[str]:
    notes = [str(warning) for warning in report.coverage.get("warnings") or []]
    if report.publication_status != "published":
        notes.insert(0, f"UNPUBLISHED — editorial status: {report.publication_status}. This copy has not been published.")
    else:
        notes.insert(0, "Publication: published." + (" Automatically published; no human approval was required." if not report.review_required else ""))
    grounding = report.coverage.get("grounding")
    if not isinstance(grounding, dict) or grounding.get("version") != 1:
        return notes
    status = grounding.get("status")
    if status == "human_edited":
        notes.append("The narrative was edited by a person. Review the retained source evidence; original AI grounding counters no longer describe this revision.")
        return notes
    if status not in {"checked", "degraded", "insufficient_evidence"}:
        return notes
    counts = [grounding.get("validated_findings"), grounding.get("cited_claim_blocks")]
    if all(isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_RENDER_NODES for value in counts):
        notes.append(f"Source checks: {counts[0]} findings validated; {counts[1]} cited claim blocks checked.")
    if status == "degraded":
        notes.append("Source checks are incomplete; some report evidence was insufficient.")
    elif status == "insufficient_evidence":
        notes.append("Insufficient source evidence was available for a grounded report.")
    notes.append("These are structural source checks. They do not verify whether each claim is true or supported by its source.")
    return notes

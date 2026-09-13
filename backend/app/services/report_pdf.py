"""Offline, paginated PDF rendering of the shared safe Markdown structure."""
from __future__ import annotations

import io
from functools import lru_cache
from html import escape
from pathlib import Path
from threading import Lock

import reportlab
from markdown_it.tree import SyntaxTreeNode
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from app.schemas.reports import ReportDetailResponse
from app.services.report_markdown import (
    MAX_PDF_PAGES, ReportMarkdown, ReportRenderingLimitError, heading_offset,
    coverage_notes, inline_markup, safe_external_url, source_anchor,
)

_FONT_LOCK = Lock()
PAGE_WIDTH = A4[0] - 36 * mm - 12  # SimpleDocTemplate frame padding


@lru_cache(maxsize=1)
def _register_fonts() -> None:
    # Fixed installation paths, never paths/URLs supplied by a report or tenant.
    system = Path("/usr/share/fonts/truetype/dejavu")
    bundled = Path(reportlab.__file__).parent / "fonts"
    with _FONT_LOCK:
        if "ThreatLensMono-BoldOblique" in pdfmetrics.getRegisteredFontNames():
            return
        for family, stem in (("ThreatLens", "DejaVuSans"), ("ThreatLensMono", "DejaVuSansMono")):
            for suffix, fallback in (("", "Vera.ttf"), ("-Bold", "VeraBd.ttf"),
                                     ("-Oblique", "VeraIt.ttf"), ("-BoldOblique", "VeraBI.ttf")):
                filename = system / f"{stem}{suffix}.ttf"
                if not filename.exists():
                    filename = system / f"{stem}{'-Bold' if 'Bold' in suffix else ''}.ttf"
                if not filename.exists():
                    filename = bundled / fallback
                if filename.stat().st_size > 5 * 1024 * 1024:
                    raise ReportRenderingLimitError("Installed PDF font exceeds the font resource limit.")
                pdfmetrics.registerFont(TTFont(f"{family}{suffix}", str(filename)))
            pdfmetrics.registerFontFamily(family, normal=family, bold=f"{family}-Bold",
                                          italic=f"{family}-Oblique", boldItalic=f"{family}-BoldOblique")


class _ReportDocument(SimpleDocTemplate):
    def handle_pageBegin(self) -> None:
        if self.page >= MAX_PDF_PAGES:
            raise ReportRenderingLimitError("Report exceeds the PDF page limit.")
        super().handle_pageBegin()


def _styles() -> dict[str, ParagraphStyle]:
    body = ParagraphStyle("Body", fontName="ThreatLens", fontSize=9, leading=13, spaceAfter=6,
                          splitLongWords=True, allowWidows=0, allowOrphans=0, uriWasteReduce=0.2)
    return {
        "body": body,
        "title": ParagraphStyle("Title", parent=body, fontName="ThreatLens-Bold", fontSize=21, leading=26,
                                alignment=TA_CENTER, textColor=colors.HexColor("#0f4f49"), spaceAfter=12),
        "meta": ParagraphStyle("Meta", parent=body, fontSize=8, leading=11, alignment=TA_CENTER,
                               textColor=colors.HexColor("#52615e")),
        "section": ParagraphStyle("Section", parent=body, fontName="ThreatLens-Bold", fontSize=14, leading=18,
                                  textColor=colors.HexColor("#0f4f49"), spaceBefore=14, spaceAfter=8, keepWithNext=True),
        "code": ParagraphStyle("Code", parent=body, fontName="ThreatLensMono", fontSize=7.5, leading=10,
                               backColor=colors.HexColor("#eef3f2"), spaceAfter=0),
        "cell": ParagraphStyle("Cell", parent=body, fontSize=8, leading=11, spaceAfter=0),
    }


class _MarkdownFlowables:
    def __init__(self, sources: dict, styles: dict) -> None:
        self.sources = sources
        self.styles = styles
        self.offset = 0

    def render(self, tree: SyntaxTreeNode) -> list:
        self.offset = heading_offset(tree)
        return self.blocks(tree.children or [])

    def paragraph(self, markup: str, *, depth: int = 0, quote: int = 0, bullet: str | None = None,
                  style: ParagraphStyle | None = None) -> Paragraph:
        base = style or self.styles["body"]
        indent = min(depth, 8) * 12 + min(quote, 4) * 10
        adjusted = ParagraphStyle(f"{base.name}Indented", parent=base, leftIndent=indent,
                                  bulletIndent=max(0, indent - 11), bulletFontName="ThreatLens",
                                  borderPadding=3 if quote else 0,
                                  textColor=colors.HexColor("#52615e") if quote else base.textColor)
        return Paragraph(markup or "&#160;", adjusted, bulletText=bullet)

    def blocks(self, nodes: list[SyntaxTreeNode], *, depth: int = 0, quote: int = 0) -> list:
        result = []
        for node in nodes:
            kind = node.type
            if kind in {"html_block", "html_inline"}:
                continue
            if kind in {"paragraph", "heading"}:
                style = self.styles["body"]
                if kind == "heading":
                    level = min(6, int(node.tag[1:]) + self.offset)
                    size = max(9.5, 14 - (level - 2))
                    style = ParagraphStyle(f"MarkdownHeading{level}", parent=self.styles["section"], fontSize=size, leading=size + 4)
                result.append(self.paragraph(inline_markup(node, self.sources, pdf=True), depth=depth, quote=quote, style=style))
            elif kind in {"bullet_list", "ordered_list"}:
                start = int(node.attrGet("start") or 1)
                for index, item in enumerate(node.children or []):
                    bullet = f"{start + index}." if kind == "ordered_list" else "•"
                    children = item.children or []
                    # The marker stays with the first paragraph; long paragraphs and
                    # nested lists can split normally instead of forming an atomic box.
                    if children and children[0].type == "paragraph":
                        result.append(self.paragraph(inline_markup(children[0], self.sources, pdf=True),
                                                     depth=depth + 1, quote=quote, bullet=bullet))
                        children = children[1:]
                    else:
                        result.append(self.paragraph("", depth=depth + 1, quote=quote, bullet=bullet))
                    result.extend(self.blocks(children, depth=depth + 1, quote=quote))
            elif kind == "blockquote":
                result.extend(self.blocks(node.children or [], depth=depth, quote=quote + 1))
            elif kind in {"fence", "code_block"}:
                for line in node.content.expandtabs(4).splitlines() or [""]:
                    text = escape(line).replace(" ", "&#160;")
                    result.append(self.paragraph(text, depth=depth, quote=quote, style=self.styles["code"]))
                result.append(Spacer(1, 6))
            elif kind == "table":
                result.extend(self.table(node, depth=depth, quote=quote))
            elif kind == "hr":
                result.append(HRFlowable(width="100%", color=colors.HexColor("#cbdad7"), spaceAfter=7))
            else:
                result.extend(self.blocks(node.children or [], depth=depth, quote=quote))
        return result

    def table(self, node: SyntaxTreeNode, *, depth: int, quote: int) -> list:
        rows = [row for group in node.children or [] for row in group.children or [] if row.type == "tr"]
        if not rows:
            return []
        cells = [[inline_markup(cell, self.sources, pdf=True) for cell in row.children or []] for row in rows]
        columns = max(len(row) for row in cells)
        width = PAGE_WIDTH - min(depth, 8) * 12 - min(quote, 4) * 10
        headers = [Paragraph(value or "&#160;", self.styles["cell"]) for value in cells[0]]
        tall_header = any(cell.wrap(width / columns - 10, 100_000)[1] > 120 for cell in headers)
        if columns > 8 or tall_header:
            return self.table_records(cells, depth=depth, quote=quote, wide=columns > 8)
        data = [headers, *[[Paragraph(value or "&#160;", self.styles["cell"]) for value in row] for row in cells[1:]]]
        table = LongTable(data, colWidths=[width / columns] * columns, repeatRows=1,
                          splitByRow=1, splitInRow=1, hAlign="RIGHT", spaceAfter=8)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2efec")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbdad7")),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return [table]

    def table_records(self, cells: list[list[str]], *, depth: int, quote: int, wide: bool) -> list:
        # A repeated header cannot itself split across pages. Render unusually
        # tall headers once, and refer to them by column number in each record.
        description = "Wide table" if wide else "Table with long column headings"
        result = [self.paragraph(f"{description} displayed as labeled rows.", depth=depth, quote=quote)]
        headers = cells[0]
        labels = []
        for index, header in enumerate(headers, start=1):
            if len(header) > 120:
                result.append(self.paragraph(f"<b>Column {index}</b>: {header}", depth=depth, quote=quote))
                labels.append(f"Column {index}")
            else:
                labels.append(header)
        if len(cells) == 1:
            # Keep a header-only table meaningful even with no data rows.
            result.extend(self.paragraph(header, depth=depth, quote=quote) for header in headers if len(header) <= 120)
        for index, row in enumerate(cells[1:], start=1):
            result.append(self.paragraph(f"<b>Row {index}</b>", depth=depth, quote=quote))
            for column, value in enumerate(row):
                label = labels[column] if column < len(labels) else f"Column {column + 1}"
                result.append(self.paragraph(f"<b>{label}</b>: {value}", depth=depth, quote=quote))
        return result


def render_pdf(report: ReportDetailResponse, parsed: ReportMarkdown) -> bytes:
    _register_fonts()
    output = io.BytesIO()
    document = _ReportDocument(output, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                               topMargin=18 * mm, bottomMargin=18 * mm, title=report.title, author="ThreatLens")
    styles = _styles()
    renderer = _MarkdownFlowables(parsed.sources, styles)
    generated = report.generated_at.isoformat() if report.generated_at else "Not complete"
    meta = (f"Period {report.period_start.date().isoformat()} to {report.period_end.date().isoformat()} | "
            f"{report.included_source_count} of {report.source_count} matching sources | Generated {generated} | "
            f"Model {report.model or 'Not recorded'}")
    story = [Paragraph(escape(report.title), styles["title"]), Paragraph(escape(meta), styles["meta"]), Spacer(1, 8 * mm)]
    warnings = coverage_notes(report)
    if warnings:
        story.append(Paragraph("Coverage notes", styles["section"]))
        for warning in warnings:
            story.append(renderer.paragraph(escape(str(warning)), depth=1, bullet="•"))
    for section, tree in zip(report.sections, parsed.sections, strict=True):
        story.append(Paragraph(escape(section.title), styles["section"]))
        story.extend(renderer.render(tree))
    if parsed.sources:
        story.append(Paragraph("Source evidence", styles["section"]))
        for key, source in parsed.sources.items():
            href = safe_external_url(source.url)
            title = escape(source.title)
            if href:
                title = f'<link href="{escape(href, quote=True)}" color="#0f766e"><u>{title}</u></link>'
            markup = f'<a name="{source_anchor(key)}"/>[{key}] {title} — {escape(source.feed_name)}'
            story.append(renderer.paragraph(markup))
    document.build(story, onFirstPage=_draw_pdf_footer, onLaterPages=_draw_pdf_footer)
    return output.getvalue()


def _draw_pdf_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("ThreatLens", 8)
    canvas.setFillColor(colors.HexColor("#6b7774"))
    canvas.drawString(18 * mm, 10 * mm, "ThreatLens intelligence report")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {document.page}")
    canvas.restoreState()

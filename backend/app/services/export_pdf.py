import html
import io
import re
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.schemas.exports import ArticleExportOptions
from app.services.export_documents import isoformat_or_none
from app.services.export_models import ExportRecord

_FONT_REGISTERED = False


def build_article_pdf(record: ExportRecord, *, options: ArticleExportOptions) -> bytes:
    _register_fonts()
    output = io.BytesIO()
    document = BaseDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=19 * mm,
        bottomMargin=18 * mm,
        title=record.title,
        author="ThreatLens",
        subject="Threat intelligence article export",
    )
    frame = Frame(document.leftMargin, document.bottomMargin, document.width, document.height, id="content")
    document.addPageTemplates([PageTemplate(id="article", frames=[frame], onPage=_draw_page_chrome)])

    styles = _build_styles()
    story = [Paragraph(_safe_text(record.title), styles["Title"]), Spacer(1, 3 * mm)]
    story.append(_metadata_table(record, styles=styles))

    if options.include_ai_details and record.ai:
        story.extend(_section("AI assessment", _ai_body(record), styles=styles))
    if record.summary:
        story.extend(_section("Source summary", record.summary, styles=styles))
    if record.tags:
        tag_text = ", ".join(tag.name for tag in record.tags)
        story.extend(_section("Tags", tag_text, styles=styles))
    if options.include_iocs and record.iocs:
        ioc_rows = [["Type", "Value", "Source", "Confidence"]]
        ioc_rows.extend(
            [ioc.type, ioc.value, ioc.source_section, f"{ioc.confidence:.0%}"]
            for ioc in record.iocs
        )
        story.extend([Paragraph("Indicators and observables", styles["Heading2"]), _data_table(ioc_rows, styles)])
    if options.include_user_state:
        state_lines = [
            f"Read: {'Yes' if record.state.is_read else 'No'}",
            f"Starred: {'Yes' if record.state.is_starred else 'No'}",
        ]
        if options.include_user_notes and record.state.note:
            state_lines.extend(["", record.state.note])
        story.extend(_section("User state", "\n".join(state_lines), styles=styles))
    if options.pdf_include_article_text and record.article and record.article.text:
        story.extend(_section("Full article text", record.article.text, styles=styles))
    elif record.article is None or not record.article.text:
        story.extend(_section("Full article text", "No full article text is available.", styles=styles))

    document.build(story)
    return output.getvalue()


def build_pdf_filename(record: ExportRecord) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", record.title).strip("-").lower()[:70]
    return f"{slug or 'article'}-{str(record.id)[:8]}.pdf"


def _register_fonts() -> None:
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    font_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    pdfmetrics.registerFont(TTFont("ThreatLensSans", font_dir / "Vera.ttf"))
    pdfmetrics.registerFont(TTFont("ThreatLensSans-Bold", font_dir / "VeraBd.ttf"))
    _FONT_REGISTERED = True


def _build_styles():
    styles = getSampleStyleSheet()
    styles["Title"].fontName = "ThreatLensSans-Bold"
    styles["Title"].fontSize = 18
    styles["Title"].leading = 22
    styles["Title"].textColor = colors.HexColor("#10231d")
    styles["Heading2"].fontName = "ThreatLensSans-Bold"
    styles["Heading2"].fontSize = 11
    styles["Heading2"].leading = 14
    styles["Heading2"].spaceBefore = 8
    styles["Heading2"].spaceAfter = 4
    styles["BodyText"].fontName = "ThreatLensSans"
    styles["BodyText"].fontSize = 8.5
    styles["BodyText"].leading = 12
    styles["BodyText"].wordWrap = "CJK"
    styles.add(
        ParagraphStyle(
            name="Metadata",
            parent=styles["BodyText"],
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#334b43"),
        )
    )
    return styles


def _metadata_table(record: ExportRecord, *, styles):
    classification = record.classification.primary_category if record.classification else "Unclassified"
    relevance = "Not scored"
    if record.ai and record.ai.relevance_label:
        score = f" ({record.ai.relevance_score:.0%})" if record.ai.relevance_score is not None else ""
        relevance = f"{record.ai.relevance_label.title()}{score}"
    rows = [
        ["Source", record.feed_name],
        ["Published", isoformat_or_none(record.published_at) or "Unknown"],
        ["Classification", classification],
        ["AI relevance", relevance],
        ["URL", record.url],
    ]
    rendered = [
        [Paragraph(f"<b>{_safe_text(label)}</b>", styles["Metadata"]), Paragraph(_safe_text(value), styles["Metadata"])]
        for label, value in rows
    ]
    table = Table(rendered, colWidths=[31 * mm, 125 * mm], repeatRows=0)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eaf4ef")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9bb8ad")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _data_table(rows: list[list[str]], styles):
    rendered = [[Paragraph(_safe_text(cell), styles["Metadata"]) for cell in row] for row in rows]
    table = Table(rendered, colWidths=[25 * mm, 75 * mm, 30 * mm, 25 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#163f34")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#9bb8ad")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _section(title: str, body: str, *, styles) -> list:
    paragraphs = [Paragraph(_safe_text(line) or "&nbsp;", styles["BodyText"]) for line in body.splitlines()]
    return [KeepTogether([Paragraph(title, styles["Heading2"]), paragraphs[0]]), *paragraphs[1:]]


def _ai_body(record: ExportRecord) -> str:
    if record.ai is None:
        return "No AI assessment is available."
    lines: list[str] = []
    if record.ai.summary:
        lines.append(record.ai.summary)
    if record.ai.relevance_label:
        score = f" ({record.ai.relevance_score:.0%})" if record.ai.relevance_score is not None else ""
        lines.append(f"Relevance: {record.ai.relevance_label.title()}{score}")
    if record.ai.relevance_reasons:
        lines.extend(f"- {reason}" for reason in record.ai.relevance_reasons)
    return "\n".join(lines) or "No AI assessment is available."


def _safe_text(value: object) -> str:
    text = str(value).replace("\x00", " ")
    text = "".join(character if character in "\n\t" or ord(character) >= 32 else " " for character in text)
    return html.escape(text)


def _draw_page_chrome(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("ThreatLensSans", 7)
    canvas.setFillColor(colors.HexColor("#4d645c"))
    canvas.drawString(document.leftMargin, 9 * mm, "ThreatLens article export")
    canvas.drawRightString(A4[0] - document.rightMargin, 9 * mm, f"Page {document.page}")
    canvas.setStrokeColor(colors.HexColor("#9bb8ad"))
    canvas.setLineWidth(0.35)
    canvas.line(document.leftMargin, 13 * mm, A4[0] - document.rightMargin, 13 * mm)
    canvas.restoreState()

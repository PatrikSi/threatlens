from __future__ import annotations

import io
import re
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from app.schemas.reports import ReportDetailResponse


def render_report_markdown(report: ReportDetailResponse) -> str:
    lines = [
        f"# {report.title}",
        "",
        f"**Period:** {report.period_start.date().isoformat()} to {report.period_end.date().isoformat()}  ",
        f"**Sources:** {report.included_source_count} included of {report.source_count} matching  ",
        f"**Generated:** {report.generated_at.isoformat() if report.generated_at else 'Not complete'}  ",
        f"**Model:** {report.model or 'Not recorded'}",
        "",
    ]
    warnings = list(report.coverage.get("warnings") or [])
    if warnings:
        lines.extend(["## Coverage Notes", "", *[f"- {warning}" for warning in warnings], ""])
    for section in report.sections:
        lines.extend([f"## {section.title}", "", section.body_markdown or "_No content generated._", ""])
    if not any(section.key == "sources" for section in report.sections):
        lines.extend(["## Sources", ""])
        lines.extend(
            f"- [{source.citation_key}] [{source.title}]({source.url}) - {source.feed_name}"
            for source in report.sources
            if source.included
        )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_report_html(report: ReportDetailResponse) -> str:
    section_html = "".join(
        f"<section><h2>{escape(section.title)}</h2>{_markdown_fragment_to_html(section.body_markdown)}</section>"
        for section in report.sections
    )
    warning_html = ""
    warnings = list(report.coverage.get("warnings") or [])
    if warnings:
        warning_html = "<aside><h2>Coverage notes</h2><ul>" + "".join(
            f"<li>{escape(str(warning))}</li>" for warning in warnings
        ) + "</ul></aside>"
    generated = report.generated_at.isoformat() if report.generated_at else "Not complete"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(report.title)}</title>
<style>
body{{font-family:Inter,Arial,sans-serif;max-width:920px;margin:0 auto;padding:40px 28px;color:#17211f;line-height:1.58}}
header{{border-bottom:3px solid #0f766e;padding-bottom:20px;margin-bottom:28px}}h1{{font-size:30px;line-height:1.2;margin:0 0 12px}}
h2{{font-size:20px;margin:30px 0 10px;color:#0f4f49}}.meta{{color:#52615e;font-size:13px}}section,aside{{border-bottom:1px solid #d9e1df;padding-bottom:18px}}
aside{{background:#f4f8f7;border:1px solid #cbdad7;padding:8px 18px;margin-bottom:24px}}code{{background:#eef3f2;padding:2px 4px}}
a{{color:#0f766e;overflow-wrap:anywhere}}li{{margin:5px 0}}@media print{{body{{padding:0}}aside{{break-inside:avoid}}}}
</style>
</head>
<body>
<header>
<h1>{escape(report.title)}</h1>
<div class="meta">Period {report.period_start.date().isoformat()} to {report.period_end.date().isoformat()} |
{report.included_source_count} of {report.source_count} matching sources | Generated {escape(generated)} |
Model {escape(report.model or 'Not recorded')}</div>
</header>
{warning_html}{section_html}
</body></html>"""


def render_report_pdf(report: ReportDetailResponse) -> bytes:
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=report.title,
        author="ThreatLens",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ThreatLensTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=27,
        textColor=colors.HexColor("#0f4f49"),
        alignment=TA_CENTER,
        spaceAfter=12,
    )
    heading_style = ParagraphStyle(
        "ThreatLensHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#0f4f49"),
        spaceBefore=12,
        spaceAfter=7,
    )
    body_style = ParagraphStyle(
        "ThreatLensBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        spaceAfter=5,
    )
    meta_style = ParagraphStyle("ThreatLensMeta", parent=body_style, textColor=colors.HexColor("#52615e"), alignment=TA_CENTER)
    story = [
        Paragraph(escape(report.title), title_style),
        Paragraph(
            escape(
                f"Period {report.period_start.date().isoformat()} to {report.period_end.date().isoformat()} | "
                f"{report.included_source_count} of {report.source_count} matching sources | Model {report.model or 'Not recorded'}"
            ),
            meta_style,
        ),
        Spacer(1, 8 * mm),
    ]
    warnings = list(report.coverage.get("warnings") or [])
    if warnings:
        story.append(Paragraph("Coverage Notes", heading_style))
        for warning in warnings:
            story.append(Paragraph(f"- {escape(str(warning))}", body_style))
    for index, section in enumerate(report.sections):
        if index and section.key == "sources":
            story.append(PageBreak())
        story.append(Paragraph(escape(section.title), heading_style))
        paragraphs = _markdown_to_plain_paragraphs(section.body_markdown)
        for paragraph in paragraphs or ["No content generated."]:
            story.append(Paragraph(escape(paragraph), body_style))
    document.build(story, onFirstPage=_draw_pdf_footer, onLaterPages=_draw_pdf_footer)
    return output.getvalue()


def _markdown_fragment_to_html(value: str) -> str:
    lines = str(value or "").splitlines()
    output: list[str] = []
    list_open = False
    for raw in lines:
        line = raw.strip()
        if not line:
            if list_open:
                output.append("</ul>")
                list_open = False
            continue
        if line.startswith("- "):
            if not list_open:
                output.append("<ul>")
                list_open = True
            output.append(f"<li>{_inline_markdown(line[2:])}</li>")
            continue
        if list_open:
            output.append("</ul>")
            list_open = False
        output.append(f"<p>{_inline_markdown(line)}</p>")
    if list_open:
        output.append("</ul>")
    return "".join(output) or "<p><em>No content generated.</em></p>"


def _inline_markdown(value: str) -> str:
    safe = escape(value)
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    return safe


def _markdown_to_plain_paragraphs(value: str) -> list[str]:
    paragraphs: list[str] = []
    for line in str(value or "").splitlines():
        text = line.strip()
        if not text:
            continue
        text = re.sub(r"^#{1,6}\s+", "", text)
        text = re.sub(r"\[([^]]+)]\(([^)]+)\)", r"\1 (\2)", text)
        text = text.replace("**", "").replace("`", "")
        paragraphs.append(text)
    return paragraphs


def _draw_pdf_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6b7774"))
    canvas.drawString(18 * mm, 10 * mm, "ThreatLens intelligence report")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {document.page}")
    canvas.restoreState()

from __future__ import annotations

from html import escape

from app.schemas.reports import ReportDetailResponse
from app.services.report_markdown import coverage_notes, html_fragment, parse_report, safe_external_url, source_anchor
from app.services.report_pdf import render_pdf


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
    parsed = parse_report(report)
    section_html = "".join(
        f"<section><h2>{escape(section.title)}</h2>{html_fragment(tree, parsed.sources)}</section>"
        for section, tree in zip(report.sections, parsed.sections, strict=True)
    )
    warning_html = ""
    warnings = coverage_notes(report)
    if warnings:
        warning_html = "<aside><h2>Coverage notes</h2><ul>" + "".join(
            f"<li>{escape(str(warning))}</li>" for warning in warnings
        ) + "</ul></aside>"
    source_html = ""
    if parsed.sources:
        source_html = '<section aria-label="Source evidence"><h2>Source evidence</h2><ul>'
        for key, source in parsed.sources.items():
            title = escape(source.title)
            href = safe_external_url(source.url)
            if href:
                title = f'<a href="{escape(href, quote=True)}">{title}</a>'
            source_html += f'<li id="{source_anchor(key)}" tabindex="-1">[{key}] {title} — {escape(source.feed_name)}</li>'
        source_html += "</ul></section>"
    generated = report.generated_at.isoformat() if report.generated_at else "Not complete"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(report.title)}</title>
<style>
body{{font-family:Arial,sans-serif;max-width:920px;margin:0 auto;padding:40px 28px;color:#17211f;line-height:1.58;overflow-wrap:anywhere}}
header{{border-bottom:3px solid #0f766e;padding-bottom:20px;margin-bottom:28px}}h1{{font-size:30px;line-height:1.2;margin:0 0 12px}}
h2{{font-size:20px;margin:30px 0 10px;color:#0f4f49}}h3,h4,h5,h6{{color:#0f4f49;line-height:1.3}}
.meta{{color:#52615e;font-size:13px}}section,aside{{border-bottom:1px solid #d9e1df;padding-bottom:18px}}
aside{{background:#f4f8f7;border:1px solid #cbdad7;padding:8px 18px;margin-bottom:24px}}
code{{background:#eef3f2;padding:2px 4px}}pre{{background:#eef3f2;padding:12px;overflow-x:auto;white-space:pre-wrap;overflow-wrap:anywhere}}
pre code{{padding:0}}blockquote{{border-left:3px solid #cbdad7;margin-left:0;padding-left:16px;color:#52615e}}
a{{color:#0f766e;overflow-wrap:anywhere}}a:focus-visible,[tabindex]:focus{{outline:2px solid #0f766e;outline-offset:3px}}li{{margin:5px 0}}
.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #cbdad7;padding:8px;text-align:left;vertical-align:top}}
th{{background:#e2efec}}@media print{{body{{padding:0}}thead{{display:table-header-group}}pre{{white-space:pre-wrap}}}}
</style>
</head>
<body>
<header>
<h1>{escape(report.title)}</h1>
<div class="meta">Period {report.period_start.date().isoformat()} to {report.period_end.date().isoformat()} |
{report.included_source_count} of {report.source_count} matching sources | Generated {escape(generated)} |
Model {escape(report.model or 'Not recorded')}</div>
</header>
{warning_html}{section_html}{source_html}
</body></html>"""


def render_report_pdf(report: ReportDetailResponse) -> bytes:
    return render_pdf(report, parse_report(report))

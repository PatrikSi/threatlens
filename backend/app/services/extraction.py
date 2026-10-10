from bs4 import BeautifulSoup
from readability import Document
import trafilatura

from app.services.article_appendices import MAX_HTML_CHARS, separate_appendices


def extract_canonical_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    node = soup.find("link", attrs={"rel": lambda rel: rel and "canonical" in rel})
    if node and node.get("href"):
        return str(node.get("href")).strip()
    return None


def extract_plain_text(html_or_text: str) -> str:
    text = BeautifulSoup(html_or_text, "lxml").get_text("\n", strip=True)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def extract_readable_text(html: str) -> dict[str, str | int | None]:
    if len(html) > MAX_HTML_CHARS:
        return {"text": None, "method": "none", "title": None, "language": None,
                "word_count": None, "error": "article_extraction_size_limit"}
    appendices = separate_appendices(html)
    prose_html = appendices.prose_html
    trafilatura_text = trafilatura.extract(prose_html, include_tables=False, include_images=False)
    if trafilatura_text:
        article_text = _join_evidence(trafilatura_text, appendices.text)
        return {
            "text": article_text,
            "method": "trafilatura",
            "title": None,
            "language": None,
            "word_count": len(article_text.split()),
            "error": None,
        }

    try:
        doc = Document(prose_html)
        title = doc.short_title()
        summary_html = doc.summary()
        text = _join_evidence(extract_plain_text(summary_html), appendices.text)
        if text:
            return {
                "text": text,
                "method": "readability",
                "title": title,
                "language": None,
                "word_count": len(text.split()),
                "error": None,
            }
    except Exception as exc:
        if appendices.text:
            return {"text": appendices.text, "method": "structured_html", "title": None,
                    "language": None, "word_count": len(appendices.text.split()), "error": None}
        return {
            "text": None,
            "method": "none",
            "title": None,
            "language": None,
            "word_count": None,
            "error": f"readability_error:{exc}",
        }

    return {
        "text": None,
        "method": "none",
        "title": None,
        "language": None,
        "word_count": None,
        "error": "no_extractor_succeeded",
    }


def _join_evidence(prose: str, appendix: str) -> str:
    return "\n\n".join(value for value in (prose.strip(), appendix.strip()) if value)

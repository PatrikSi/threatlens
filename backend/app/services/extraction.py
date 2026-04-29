from bs4 import BeautifulSoup
from readability import Document
import trafilatura


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
    trafilatura_text = trafilatura.extract(html, include_tables=False, include_images=False)
    if trafilatura_text:
        return {
            "text": trafilatura_text,
            "method": "trafilatura",
            "title": None,
            "language": None,
            "word_count": len(trafilatura_text.split()),
            "error": None,
        }

    try:
        doc = Document(html)
        title = doc.short_title()
        summary_html = doc.summary()
        text = extract_plain_text(summary_html)
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

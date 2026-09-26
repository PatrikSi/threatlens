"""Retain bounded table and code evidence independently of readable prose."""

from dataclasses import dataclass
import re

from bs4 import BeautifulSoup, Tag


MAX_HTML_CHARS = 4_000_000
MAX_APPENDIX_BLOCKS = 64
MAX_APPENDIX_BLOCK_CHARS = 16_000
MAX_APPENDIX_CHARS = 128_000


@dataclass(frozen=True)
class ArticleAppendices:
    prose_html: str
    text: str
    truncated: bool


def separate_appendices(html: str) -> ArticleAppendices:
    """Remove selected blocks before prose extraction to avoid duplicate content.

    Limits are visible in the retained text. The original HTML remains available
    in the article artifact; clipping never claims to cover an entire appendix.
    """
    soup = BeautifulSoup(html, "lxml")
    scope = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = [node for node in scope.find_all(("table", "pre", "code"))
              if not node.find_parent(("table", "pre", "code"))
              and not node.find_parent(("nav", "header", "footer", "aside", "script", "style"))]
    retained: list[str] = []
    seen: set[str] = set()
    used = 0
    truncated = False
    for index, node in enumerate(blocks):
        if index >= MAX_APPENDIX_BLOCKS or used >= MAX_APPENDIX_CHARS:
            truncated = True
            node.decompose()
            continue
        content = _block_text(node)
        remaining = min(MAX_APPENDIX_BLOCK_CHARS, MAX_APPENDIX_CHARS - used)
        if len(content) > remaining:
            truncated = True
            # A partial network token must never become a different indicator.
            clipped = content[:remaining]
            split = max(clipped.rfind("\n"), clipped.rfind("\t"), clipped.rfind(" "))
            content = clipped[:max(0, split)]
        signature = re.sub(r"\s+", " ", content).strip()
        if signature and signature not in seen:
            retained.append(content)
            seen.add(signature)
            used += len(content)
        node.decompose()
    appendix = "\n\n".join(retained)
    if truncated:
        appendix += "\n\n[Article table/code appendix was truncated by the extraction limit.]"
    return ArticleAppendices(str(soup), appendix.strip(), truncated)


def _block_text(node: Tag) -> str:
    if node.name != "table":
        return _structured_text(node)
    rows = []
    for row in node.find_all("tr"):
        cells = row.find_all(("th", "td"), recursive=False)
        if cells:
            rows.append("\t".join(_structured_text(cell) for cell in cells))
    return "\n".join(rows) if rows else node.get_text("\n", strip=True)


def _structured_text(node: Tag) -> str:
    # Inline syntax highlighting must not split a defanged value into words.
    # Conversely explicit line/block breaks must not concatenate two values.
    for line_break in node.find_all("br"):
        line_break.replace_with("\n")
    for block in node.find_all(("p", "div", "li")):
        block.append("\n")
    return node.get_text("", strip=False).strip()

"""Parse report structure without deciding which destinations may be opened."""
from __future__ import annotations

from markdown_it import MarkdownIt


def report_markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": True, "maxNesting": 32}).enable(["table", "strikethrough"])
    # React Markdown recognizes these nodes even when its URL transform later
    # suppresses the destination. Treating unsafe schemes as ordinary text here
    # could count a literal image/link label as a navigable citation. Consumers
    # must render the AST with the report's own escaping and URL policy, never
    # with this parser's HTML renderer. Images and raw HTML are always omitted.
    parser.validateLink = lambda _url: True
    return parser

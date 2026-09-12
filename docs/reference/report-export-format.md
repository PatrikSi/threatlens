# Report export formatting

HTML and PDF exports parse report sections with `markdown-it-py` 4.0.0 using CommonMark plus tables and strikethrough. The web report reader supports the same principal formatting: headings, paragraphs, nested and ordered lists, emphasis, block quotes, fenced or indented code, tables, and links. Export formatting does not regenerate or rewrite a report's claims.

Markdown downloads retain the original section text. They remain available when an HTML/PDF export exceeds a rendering limit.

## Supported structure

| Content | HTML | PDF |
| --- | --- | --- |
| Headings | Semantic headings below the report title and section titles | Distinct heading sizes below section headings |
| Lists | Native nested lists; ordered start numbers preserved | Indented, numbered or bulleted paragraphs that can split across pages |
| Emphasis and quotes | Strong, emphasis, strikethrough and block quotes | Bold, italic, strikethrough and indented quote text |
| Code | Escaped text with preserved whitespace; no highlighting scripts | Embedded monospace font, preserved indentation and wrapped long lines |
| Tables | Semantic column headers and a horizontal scroll container | Repeated headers, wrapped cells and rows that can split across pages |
| Source citations | Known included `[S1]` references link to focusable source evidence | Known included references link to source evidence within the PDF |

Both structured formats append the included source evidence independently of whether the report template has a Sources section. This keeps citation destinations available for custom templates. Unknown or excluded citations remain plain text. References inside code remain code; citations inside an existing external link do not create nested links.

In the web reader, a citation immediately following a bare URL remains a source reference. If automatic URL linking would consume part of that marker, the URL stays literal text. Explicit Markdown links, angle-bracket autolinks and code retain their original meaning; markers inside those links or code are not counted as source citations.

PDF tables with more than eight columns, or with column headings taller than 120 points at the rendered width, become labeled records. No cell values are dropped. Long headings appear once with column numbers used in the records. This keeps wide or unusually tall tables readable on A4 pages.

## Untrusted content and source checks

The parser recognizes raw HTML only so the export renderers can omit it. The renderers emit an allowlist of formatting elements; report text never becomes arbitrary HTML or ReportLab markup. Images become text placeholders. Rendering never downloads images, fonts, stylesheets, or other publisher resources.

Clickable external links must use HTTP or HTTPS and cannot contain credentials, control characters or backslashes. Unsafe link syntax may remain visible as plain text. HTML adds a restrictive Content Security Policy as defense in depth. Following an allowed external link is an explicit action in the browser or PDF viewer.

Generation requires citations on substantive numeric data as well as prose: dates, counts, percentages and numeric indicators in paragraphs, list bodies or table data rows. Empty ordered-list markers, table column labels, horizontal rules and punctuation-only decorative rows are structural content rather than claims. This is a citation-presence check, not a determination that a number is correct.

When a report includes grounding metadata, coverage notes show the recorded finding and cited-claim counts and any degraded or insufficient-evidence state. These are structural source checks. They do not establish that every statement is true or semantically supported by its cited source. Older reports without this metadata remain readable.

New report snapshots record `coverage.evidence_contract_version = 1`: their planner admits prior AI summaries and relevance only when successful enrichment matches the source version at planning. Queued or retried snapshots without this version, or with an unknown version, stop before generation or paid-stage replay with `source_snapshot_requires_rebuild`. Create a new report from current sources. Retry retains the same immutable evidence and cannot upgrade its provenance. This conservative gate also covers legacy primary-only snapshots because the old free-form evidence did not reliably identify its origin. Completed historical reports remain readable and exportable; existing saved stages are retained without replay or new provider calls.

## Fonts, limits and accessibility

The backend image installs DejaVu normal, bold, italic and monospace fonts; PDF output embeds the required font subsets. This supports Latin, Greek and Cyrillic text and common symbols. DejaVu does not provide complete CJK coverage, emoji, complex-script shaping or reliable right-to-left layout. Use HTML or Markdown and a suitable local font/reader for those languages. Noncontainer installations should install Debian's `fonts-dejavu-core` and `fonts-dejavu-extra` (or provide the same trusted font paths). Without DejaVu, the renderer falls back to ReportLab's bundled Vera fonts with more limited character coverage.

Structured exports accept at most 4 MiB of UTF-8 report text and metadata, 256 KiB per section, 100,000 structural units (metadata entries, lines and parsed nodes), and 32 levels of Markdown nesting. PDF output is limited to 200 pages. Installed font files are read only from fixed trusted paths, must be at most 5 MiB each, and are registered once per process. These bounds limit materialization and pagination work; they are not a hard process CPU deadline. An export exceeding a byte, structure or page limit returns HTTP 413 with instructions to download Markdown or reduce the report size. Content is not silently truncated to fit.

HTML uses native headings, lists, table headers and keyboard-accessible links. PDF text is selectable and source links are navigable, but the generated PDF is not a tagged PDF/UA document. Prefer the web reader or HTML for assistive reading. Automated structural and keyboard tests do not replace a manual screen-reader check.

## Verification and dependency notices

`backend/tests/unit/test_report_rendering.py` uses synthetic report detail objects and real HTML/PDF artifacts. It checks semantic structure, source destinations and URL annotations, Unicode/font use, active-content omission, multi-page table/code/list continuity, wide/tall table fallback, grounding notes and actionable limits. It does not require a database or AI provider.

`tests/fixtures/report-citation-corpus.json` is shared by the backend validator/HTML/PDF tests and the real React DOM tests. It records accepted and rejected numeric claims, literal URL boundaries, explicit links, escaped/entity markers, code and hidden HTML, including the expected source-anchor count in each renderer.

The image's existing dependency-inventory build step copies the Markdown parser's wheel licenses and DejaVu's Debian copyright notices under `/usr/share/doc/threatlens`. Checked-in inventories in `docs/reference/` are generated from the built runtime image; PDF inspection dependencies belong only to development requirements.

Parser references: [HTML entity decoder](https://github.com/wooorm/parse-entities), [markdown-it-py usage](https://markdown-it-py.readthedocs.io/en/latest/using.html), [security guidance](https://markdown-it-py.readthedocs.io/en/latest/security.html), and [syntax-tree API](https://markdown-it-py.readthedocs.io/en/latest/api/markdown_it.tree.html).

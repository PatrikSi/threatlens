from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.config import Settings
from app.models.article import Article
from app.models.item import Item
from app.services.safe_fetch import RedirectError, SafeFetchError, build_safe_http_client, safe_stream_with_redirects
from app.services.url_utils import is_fetchable_url, normalize_url


ARTICLE_PREVIEW_CSP = (
    "default-src 'none'; "
    "base-uri http: https:; "
    "img-src http: https: data:; "
    "style-src http: https: 'unsafe-inline'; "
    "font-src http: https: data:; "
    "media-src http: https: data:; "
    "script-src 'none'; "
    "connect-src 'none'; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'self'; "
    "sandbox allow-popups allow-popups-to-escape-sandbox"
)
ARTICLE_PREVIEW_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": ARTICLE_PREVIEW_CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
_BLOCKED_TAGS = {"script", "iframe", "frame", "frameset", "object", "embed", "applet"}
_BLOCKED_META_HTTP_EQUIV = {
    "content-security-policy",
    "content-security-policy-report-only",
    "refresh",
    "set-cookie",
    "x-frame-options",
}
_URL_ATTRS = {"href", "src", "poster", "action"}
_URL_LIST_ATTRS = {"srcset"}
_BLOCKED_URL_SCHEMES = ("javascript:", "vbscript:")


@dataclass(frozen=True)
class ArticlePreviewDocument:
    html: str
    source_url: str
    final_url: str
    content_type: str | None


class ArticlePreviewFetchError(RuntimeError):
    def __init__(self, detail: str, *, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def resolve_article_preview_url(item: Item, article: Article | None) -> str | None:
    candidates = [
        article.final_url
        if article and article.http_status == 200 and "text/html" in (article.content_type or "").lower()
        else None,
        item.canonical_url,
        item.url,
    ]
    seen: set[str] = set()
    for candidate in candidates:
        normalized = normalize_url(candidate)
        if normalized and normalized not in seen:
            return normalized
        if normalized:
            seen.add(normalized)
    return None


def fetch_article_preview_document(
    item: Item,
    article: Article | None,
    *,
    settings: Settings,
) -> ArticlePreviewDocument:
    source_url = resolve_article_preview_url(item, article)
    if source_url is None:
        raise ArticlePreviewFetchError("Article preview URL is unavailable", status_code=404)
    if not is_fetchable_url(source_url, allow_private_network=settings.allow_private_network_fetch):
        raise ArticlePreviewFetchError("Article preview URL is not allowed for outbound fetch", status_code=422)

    timeout = httpx.Timeout(
        connect=settings.article_connect_timeout_seconds,
        read=settings.article_read_timeout_seconds,
        write=settings.article_read_timeout_seconds,
        pool=settings.article_connect_timeout_seconds,
    )
    try:
        with build_safe_http_client(
            timeout=timeout,
            headers={"User-Agent": settings.fetch_user_agent},
            allow_private_network=settings.allow_private_network_fetch,
        ) as client:
            response = safe_stream_with_redirects(
                client,
                "GET",
                source_url,
                allow_private_network=settings.allow_private_network_fetch,
                max_redirects=settings.outbound_max_redirects,
            )
            try:
                status_code = response.status_code
                content_type = response.headers.get("content-type")
                final_url = normalize_url(str(response.url)) or source_url
                if status_code != 200:
                    raise ArticlePreviewFetchError(
                        f"Article preview source returned HTTP {status_code}",
                        status_code=502,
                    )
                if "text/html" not in (content_type or "").lower():
                    raise ArticlePreviewFetchError("Article preview source is not an HTML page", status_code=415)

                body_chunks: list[bytes] = []
                body_size = 0
                for chunk in response.iter_bytes():
                    body_size += len(chunk)
                    if body_size > settings.article_max_bytes:
                        raise ArticlePreviewFetchError("Article preview source exceeds the configured size limit", status_code=413)
                    body_chunks.append(chunk)
            finally:
                response.close()
    except ArticlePreviewFetchError:
        raise
    except (httpx.HTTPError, TimeoutError, SafeFetchError, RedirectError) as exc:
        raise ArticlePreviewFetchError("Article preview source could not be fetched", status_code=502) from exc

    html = b"".join(body_chunks).decode("utf-8", errors="replace")
    return ArticlePreviewDocument(
        html=sanitize_article_preview_html(html, final_url=final_url),
        source_url=source_url,
        final_url=final_url,
        content_type=content_type,
    )


def sanitize_article_preview_html(html: str, *, final_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    _ensure_document_shell(soup)

    for node in soup.find_all(_BLOCKED_TAGS):
        node.decompose()

    for meta in soup.find_all("meta"):
        http_equiv = str(meta.get("http-equiv") or "").strip().lower()
        if http_equiv in _BLOCKED_META_HTTP_EQUIV:
            meta.decompose()

    for base in soup.find_all("base"):
        base.decompose()

    head = soup.head
    if head is not None:
        base = soup.new_tag("base", href=final_url)
        head.insert(0, base)

    for element in soup.find_all(True):
        for attr in list(element.attrs):
            attr_name = attr.lower()
            if attr_name.startswith("on") or attr_name == "srcdoc":
                del element.attrs[attr]
                continue
            if attr_name in _URL_ATTRS and _is_blocked_url_value(element.get(attr)):
                del element.attrs[attr]
                continue
            if attr_name in _URL_LIST_ATTRS and _is_blocked_url_list_value(element.get(attr)):
                del element.attrs[attr]

        if element.name == "a":
            href = element.get("href")
            if isinstance(href, str) and href.strip() and not _is_blocked_url_value(href):
                element["href"] = urljoin(final_url, href)
                element["target"] = "_blank"
                element["rel"] = "noopener noreferrer"

    return str(soup)


def _ensure_document_shell(soup: BeautifulSoup) -> None:
    if soup.html is None:
        html = soup.new_tag("html")
        for child in list(soup.contents):
            html.append(child.extract())
        soup.append(html)

    if soup.head is None:
        soup.html.insert(0, soup.new_tag("head"))

    if soup.body is None:
        body = soup.new_tag("body")
        for child in list(soup.html.contents):
            if child is soup.head:
                continue
            body.append(child.extract())
        soup.html.append(body)


def _is_blocked_url_value(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized.startswith(_BLOCKED_URL_SCHEMES)


def _is_blocked_url_list_value(value: object) -> bool:
    if not isinstance(value, str):
        return False
    for candidate in value.split(","):
        url_part = candidate.strip().split(maxsplit=1)[0]
        if _is_blocked_url_value(url_part):
            return True
    return False

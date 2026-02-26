from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol


@dataclass
class NormalizedItem:
    guid: str | None
    url: str | None
    title: str
    summary: str | None
    published_at: datetime | None
    raw: dict[str, Any] | None = None


@dataclass
class FullTextResult:
    final_url: str
    http_status: int
    content_type: str | None
    title_extracted: str | None
    text: str | None
    extraction_method: str | None
    language: str | None
    word_count: int | None
    fetch_ms: int | None
    error: str | None


class Connector(Protocol):
    name: str

    def poll(self, source_config: dict[str, Any], cursor: dict[str, Any] | None) -> tuple[list[NormalizedItem], dict[str, Any] | None]:
        ...

    def supports_fulltext(self) -> bool:
        ...

    def fetch_fulltext(self, item: NormalizedItem) -> Optional[FullTextResult]:
        ...

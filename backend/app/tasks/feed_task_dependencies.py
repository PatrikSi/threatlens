"""Small immutable orchestration contracts for independently testable feed runners.

Stable parsing, storage, policy and network services are imported by their owners.
Only session creation, configuration and callbacks which close over Celery task
registration cross this boundary. A running task never receives a mutable module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.item import Item

SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True, slots=True)
class FeedFetchOptions:
    feed_connect_timeout_seconds: float
    feed_read_timeout_seconds: float
    feed_total_timeout_seconds: float
    feed_max_bytes: int
    fetch_user_agent: str
    allow_private_network_fetch: bool
    outbound_max_redirects: int

    @classmethod
    def from_settings(cls, settings: Settings) -> FeedFetchOptions:
        return cls(
            settings.feed_connect_timeout_seconds,
            settings.feed_read_timeout_seconds,
            settings.feed_total_timeout_seconds,
            settings.feed_max_bytes,
            settings.fetch_user_agent,
            settings.allow_private_network_fetch,
            settings.outbound_max_redirects,
        )


@dataclass(frozen=True, slots=True)
class ArticleFetchOptions:
    article_connect_timeout_seconds: float
    article_read_timeout_seconds: float
    article_total_timeout_seconds: float
    article_max_bytes: int
    fetch_user_agent: str
    allow_private_network_fetch: bool
    outbound_max_redirects: int

    @classmethod
    def from_settings(cls, settings: Settings) -> ArticleFetchOptions:
        return cls(
            settings.article_connect_timeout_seconds,
            settings.article_read_timeout_seconds,
            settings.article_total_timeout_seconds,
            settings.article_max_bytes,
            settings.fetch_user_agent,
            settings.allow_private_network_fetch,
            settings.outbound_max_redirects,
        )


class QueueAIEnrichment(Protocol):
    def __call__(
        self,
        *,
        item_id: uuid.UUID,
        trigger_source: str,
        reason: str | None,
        actor_user_id: uuid.UUID | None = None,
        parent_run_id: uuid.UUID | None = None,
        force: bool = False,
        model: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> bool: ...


class RecordSkippedAIEnrichment(Protocol):
    def __call__(
        self,
        *,
        item_id: uuid.UUID,
        trigger_source: str,
        reason: str,
        actor_user_id: uuid.UUID | None = None,
        parent_run_id: uuid.UUID | None = None,
        model: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> uuid.UUID: ...


@dataclass(frozen=True, slots=True)
class FeedFetchDependencies:
    db_session: SessionFactory
    settings: FeedFetchOptions
    enqueue_articles: Callable[[list[uuid.UUID]], bool]


@dataclass(frozen=True, slots=True)
class ArticleFetchDependencies:
    db_session: SessionFactory
    settings: ArticleFetchOptions
    enqueue_classification: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class ItemProcessingDependencies:
    db_session: SessionFactory
    enqueue_iocs: Callable[[uuid.UUID], bool]
    queue_ai_enrichment: QueueAIEnrichment
    record_skipped_ai_enrichment: RecordSkippedAIEnrichment
    is_recent_ai_candidate: Callable[[Item], bool]


@dataclass(frozen=True, slots=True)
class ItemAIDependencies:
    db_session: SessionFactory
    queue_ai_enrichment: QueueAIEnrichment
    ai_run_stop_reason: Callable[[uuid.UUID | None], str | None]

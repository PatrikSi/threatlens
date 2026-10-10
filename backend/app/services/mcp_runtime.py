"""Bounded database and admission operations used only by MCP requests."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
import time
from typing import Iterator, Literal

import redis
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.api_errors import ApiHTTPException
from app.core.config import get_settings
from app.core.redis_client import redis_client_from_url
from app.db.budgets import DatabaseDeadlineExceeded

_RATE_LIMIT_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], 60) end
return {count, redis.call('TTL', KEYS[1])}
"""


def database_failure_code(exc: Exception) -> Literal["deadline", "contention"] | None:
    """Classify stable driver codes without interpreting or exposing SQL text."""
    if not isinstance(exc, DBAPIError):
        return None
    sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    if sqlstate == "57014":  # query_canceled, including statement_timeout
        return "deadline"
    if sqlstate in {"55P03", "40P01", "40001"}:  # lock timeout, deadlock, serialization retry
        return "contention"
    return None


def remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DatabaseDeadlineExceeded("MCP request deadline exceeded")
    return remaining


@contextmanager
def mcp_database_budget(db: Session, *, deadline: float) -> Iterator[None]:
    """Apply the remaining allowance across authentication's transaction changes.

    This session is dedicated to one request. SET LOCAL lasts through the read
    transaction's eventual rollback after transfer; listeners are detached before
    handing the response to ASGI. The ordinary application engine is unchanged.
    """
    settings = get_settings()
    connections = []
    listeners = []
    configured_timeouts: dict[int, tuple[str, str]] = {}

    def listen(target, name, listener):
        event.listen(target, name, listener)
        listeners.append((target, name, listener))

    def before_statement(connection, cursor, _statement, _parameters, _context, _many):
        # Round down, never up: a short operation can share deadline settings
        # across statements without another network round trip for each elapsed
        # millisecond. The allowance is at most 49ms more conservative.
        remaining_ms = max(1, math.floor(remaining_seconds(deadline) * 20) * 50)
        if connection.dialect.name == "postgresql":
            limits = (
                str(min(remaining_ms, settings.database_statement_timeout_ms or remaining_ms)),
                str(min(remaining_ms, settings.database_lock_timeout_ms)),
            )
            # SET LOCAL survives statements, but not commit/rollback. Reuse the
            # exact values only within this transaction; still check the clock
            # before every statement and tighten as the total budget shrinks.
            if configured_timeouts.get(id(connection)) != limits:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)",
                    limits,
                )
                configured_timeouts[id(connection)] = limits

    def after_begin(_session, _transaction, connection):
        configured_timeouts.pop(id(connection), None)
        if connection not in connections:
            connections.append(connection)
            listen(connection, "before_cursor_execute", before_statement)
        remaining_seconds(deadline)

    def before_commit(_session):
        remaining_seconds(deadline)

    try:
        listen(db, "after_begin", after_begin)
        listen(db, "before_commit", before_commit)
        remaining_seconds(deadline)
        yield
        remaining_seconds(deadline)
    finally:
        for target, name, listener in reversed(listeners):
            event.remove(target, name, listener)


def enforce_mcp_rate_limit(*, bucket: str, limit: int, deadline: float) -> None:
    """A shared fixed-window budget; Redis failure denies the new read."""
    settings = get_settings()
    timeout = min(1.0, remaining_seconds(deadline) / 2)
    # Do not reuse a pool with different timeout settings across this deadline.
    key = "threatlens:mcp:rate:" + hashlib.sha256(bucket.encode("utf-8")).hexdigest()
    client = None
    try:
        client = redis_client_from_url(
            settings.redis_url,
            settings=settings.model_copy(
                update={
                    "redis_connect_timeout_seconds": timeout,
                    "redis_socket_timeout_seconds": timeout,
                }
            ),
            decode_responses=True,
        )
        count, ttl = client.eval(_RATE_LIMIT_LUA, 1, key)
        remaining_seconds(deadline)
        count, ttl = int(count), int(ttl)
        if count < 1 or ttl < 0:
            raise ValueError("Invalid admission window")
        if count > limit:
            raise ApiHTTPException(
                status_code=429,
                error_code="mcp_rate_limited",
                detail="MCP request limit reached. Retry after the indicated delay.",
                headers={"Retry-After": str(max(1, ttl))},
            )
    except (redis.RedisError, TypeError, ValueError, OverflowError) as exc:
        raise ApiHTTPException(
            status_code=503,
            error_code="mcp_admission_unavailable",
            detail="MCP admission control is unavailable. Retry shortly.",
            headers={"Retry-After": "5"},
        ) from exc
    finally:
        if client is not None:
            try:
                client.close()
            except redis.RedisError:
                # Closing a failed client must not obscure the admission decision.
                pass

from __future__ import annotations

from typing import Any

import redis

from app.core.config import Settings, get_settings


def redis_client_from_url(
    redis_url: str,
    *,
    decode_responses: bool = False,
    settings: Settings | None = None,
    **kwargs: Any,
) -> redis.Redis:
    active_settings = settings or get_settings()
    return redis.Redis.from_url(
        redis_url,
        decode_responses=decode_responses,
        socket_connect_timeout=active_settings.redis_connect_timeout_seconds,
        socket_timeout=active_settings.redis_socket_timeout_seconds,
        health_check_interval=30,
        **kwargs,
    )

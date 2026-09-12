"""One separately budgeted admission connection per application engine/process."""
from __future__ import annotations

import os
import threading
from weakref import WeakKeyDictionary

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import record_database_timeout

_engines: WeakKeyDictionary[Engine, Engine] = WeakKeyDictionary()
_lock = threading.Lock()


def provider_admission_engine(db: Session) -> Engine:
    bind = db.get_bind()
    source = bind.engine if isinstance(bind, Connection) else bind
    with _lock:
        existing = _engines.get(source)
        if existing is not None:
            return existing
        settings = get_settings()
        admission = create_engine(
            source.url, pool_size=1, max_overflow=0, pool_timeout=1,
            pool_pre_ping=True, hide_parameters=True,
            connect_args={
                "connect_timeout": min(2, settings.database_connect_timeout_seconds),
                "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10,
                "keepalives_count": 3, "tcp_user_timeout": 5000,
                "options": "-c statement_timeout=3000 -c lock_timeout=1000",
            },
        )
        event.listen(admission, "handle_error", record_database_timeout)
        _engines[source] = admission
        return admission


def dispose_provider_admission_engines(*, after_fork: bool = False) -> None:
    """Children detach inherited pools without closing the parent's sockets."""
    global _lock
    if after_fork:
        _lock = threading.Lock()
    with _lock:
        engines = list(_engines.values())
        _engines.clear()
    for engine in engines:
        engine.dispose(close=not after_fork)


def _after_fork() -> None:
    dispose_provider_admission_engines(after_fork=True)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)

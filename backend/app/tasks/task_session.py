from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.db import session as db_session_module


@contextmanager
def db_session() -> Session:
    db = db_session_module.SessionLocal()
    try:
        yield db
    finally:
        db.close()

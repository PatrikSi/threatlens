from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()


def _engine_options(database_url: str) -> dict:
    options: dict = {"pool_pre_ping": True, "hide_parameters": True}
    if make_url(database_url).get_backend_name() == "postgresql":
        options["pool_timeout"] = settings.database_pool_timeout_seconds
        options["connect_args"] = {
            "connect_timeout": settings.database_connect_timeout_seconds,
            "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
        }
    return options


engine = create_engine(settings.database_url, **_engine_options(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

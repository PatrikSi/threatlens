from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.runtime_metrics import record_runtime_event

settings = get_settings()


def _engine_options(database_url: str) -> dict:
    options: dict = {"pool_pre_ping": True, "hide_parameters": True}
    if make_url(database_url).get_backend_name() == "postgresql":
        options["pool_size"] = settings.database_pool_size
        options["max_overflow"] = settings.database_max_overflow
        options["pool_timeout"] = settings.database_pool_timeout_seconds
        options["connect_args"] = {
            "connect_timeout": settings.database_connect_timeout_seconds,
            "options": (
                f"-c statement_timeout={settings.database_statement_timeout_ms} "
                f"-c lock_timeout={settings.database_lock_timeout_ms}"
            ),
        }
    return options


engine = create_engine(settings.database_url, **_engine_options(settings.database_url))


@event.listens_for(engine, "handle_error")
def record_database_timeout(context) -> None:
    sqlstate = getattr(context.original_exception, "sqlstate", None)
    metric = {"55P03": "database_lock_timeout", "57014": "database_statement_timeout"}.get(sqlstate)
    if metric is not None:
        record_runtime_event(metric)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

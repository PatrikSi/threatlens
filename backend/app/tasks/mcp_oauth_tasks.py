"""Bounded cleanup of terminal OAuth credentials and revoked empty registrations."""

from datetime import datetime, timezone

from app.db.budgets import database_operation
from app.db.session import SessionLocal
from app.services.mcp_oauth_lifecycle import maintain_authorization
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.mcp_oauth_tasks.maintain_mcp_authorization")
def maintain_mcp_authorization() -> dict[str, int]:
    with SessionLocal() as db, database_operation(db, operation="repair"):
        result = maintain_authorization(db, now=datetime.now(timezone.utc))
        db.commit()
        return result

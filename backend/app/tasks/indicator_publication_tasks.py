"""Refresh a bounded publication slice without dispatching external actions."""

from app.db.budgets import database_operation
from app.services.indicator_publication_refresh import reconcile_publications
from app.tasks.celery_app import celery_app
from app.tasks.task_session import db_session


@celery_app.task(name="app.tasks.indicator_publication_tasks.reconcile_publications")
def reconcile_indicator_publications() -> dict[str, int]:
    with db_session() as db, database_operation(db, operation="repair"):
        checked = reconcile_publications(db, limit=10)
        db.commit()
    return {"checked": checked}

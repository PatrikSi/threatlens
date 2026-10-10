"""Bounded durable reconciliation never dispatches an external hunt."""

from datetime import datetime, timezone
from app.services.automation_health import archive_execution_history
from app.db.budgets import database_operation
from app.services.automation_executions import reconcile_executions
from app.tasks.celery_app import celery_app
from app.tasks.task_session import db_session


@celery_app.task(name="app.tasks.automation_tasks.reconcile_automation_executions")
def reconcile_automation_execution_task():
    with db_session() as db, database_operation(db, operation="repair"):
        checked = reconcile_executions(db)
        archived = archive_execution_history(db, now=datetime.now(timezone.utc))
        db.commit()
    return {"checked": checked, "archived": archived, "limit": 100}

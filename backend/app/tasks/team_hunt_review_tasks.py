"""Minute-scale local reminders; never launches hunts or external notifications."""

from app.db.budgets import database_operation
from app.db.session import SessionLocal
from app.services.team_hunt_reminders import dispatch_hunt_review_reminders
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.team_hunt_review_tasks.dispatch_hunt_review_reminders")
def dispatch_hunt_review_reminders_task() -> dict[str, int]:
    with SessionLocal() as db, database_operation(db, operation="repair"):
        count = dispatch_hunt_review_reminders(db)
        db.commit()
        return {"reminders_created": count}

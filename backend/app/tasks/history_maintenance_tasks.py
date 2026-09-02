from app.services.history_maintenance import run_application_security_housekeeping
from app.tasks.celery_app import celery_app
from app.tasks.task_session import db_session


@celery_app.task(
    name="app.tasks.history_maintenance_tasks.maintain_application_history",
    acks_late=True,
    reject_on_worker_lost=True,
)
def maintain_application_history():
    with db_session() as db:
        result = run_application_security_housekeeping(db)
    return {"status": "ok", "compatibility_mode": True, **result}

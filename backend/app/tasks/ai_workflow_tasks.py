"""Recover accepted AI work independently of the AI worker backlog."""

from app.tasks.celery_app import celery_app
from app.services.ai_workflow_publication import dispatch_due_ai_workflows


@celery_app.task(name="app.tasks.ai_workflow_tasks.dispatch_pending_ai_workflows", acks_late=True)
def dispatch_pending_ai_workflows():
    return dispatch_due_ai_workflows()

from datetime import datetime, timezone
import uuid

from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch
from app.models.team_item_assessment import TeamItemAssessment
from app.services.history_maintenance import prune_application_history
from tests.integration.test_team_assessments_api import _queue
from tests.integration.test_team_assessments_api import assessment_setup as _assessment_setup

# Explicitly register the shared pytest fixture in this module.
assessment_setup = _assessment_setup


def test_latest_assessment_run_survives_history_retention_until_replaced(
    client, db_session, assessment_setup, auth_headers,
):
    queued = _queue(client, assessment_setup, auth_headers["analyst"])
    row = db_session.get(TeamItemAssessment, uuid.UUID(queued["assessment"]["id"]))
    old_run_id = row.task_run_id
    run = db_session.get(AITaskRun, old_run_id)
    run.status = "error"
    run.error = "Retained failure status"
    run.finished_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db_session.get(AIWorkflowDispatch, run.id).state = "complete"
    db_session.commit()
    prune_application_history(db_session, now=datetime.now(timezone.utc), batch_size=100)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(AITaskRun, old_run_id).error == "Retained failure status"

    _queue(client, assessment_setup, auth_headers["analyst"], version=1)
    db_session.expire_all()
    assert db_session.get(TeamItemAssessment, row.id).task_run_id != old_run_id
    prune_application_history(db_session, now=datetime.now(timezone.utc), batch_size=100)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(AITaskRun, old_run_id) is None

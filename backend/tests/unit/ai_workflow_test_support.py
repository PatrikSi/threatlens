"""Clean committed rows created by multi-session workflow regression probes."""
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_task_run import AITaskRun
from app.models.feed import Feed


@pytest.fixture(autouse=True)
def cleanup_ai_workflow_probe(database_engine):
    with Session(database_engine) as db:
        run_ids = set(db.scalars(select(AITaskRun.id)))
        feed_ids = set(db.scalars(select(Feed.id)))
    yield
    with Session(database_engine) as db:
        db.execute(delete(AITaskRun).where(AITaskRun.id.not_in(run_ids)))
        db.execute(delete(Feed).where(Feed.id.not_in(feed_ids)))
        db.commit()

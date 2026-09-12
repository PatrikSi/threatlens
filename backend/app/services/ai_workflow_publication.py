"""Bounded AI outbox delivery with broker evidence before re-publication."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.redis_client import redis_client_from_url
from app.models.ai_task_run import AITaskRun
from app.models.ai_workflow import AIWorkflowDispatch
from app.services.ai_workflow_dispatch import (
    TERMINAL, WORKFLOW_BATCH_SIZE, WORKFLOW_CLAIM_SECONDS,
    WORKFLOW_MAX_OUTSTANDING_PUBLICATIONS, WORKFLOW_REPUBLISH_SECONDS, as_utc,
)

BROKER_SCAN_LIMIT = 2000
BROKER_UNCHECKED = object()


def queued_ai_delivery_ids() -> set[str] | None:
    """Unknown/oversized broker state never authorizes a duplicate publication."""
    from app.tasks.celery_app import QUEUE_AI
    settings = get_settings()
    client = None
    keys = [QUEUE_AI, *(f"{QUEUE_AI}\x06\x16{priority}" for priority in (3, 6, 9))]
    try:
        client = redis_client_from_url(settings.redis_url, decode_responses=True, settings=settings)
        sizes = [int(client.llen(key)) for key in keys]
        unacked_count = int(client.hlen("unacked"))
        if sum(sizes) + unacked_count > BROKER_SCAN_LIMIT:
            return None
        ids = set()
        for key, size in zip(keys, sizes, strict=True):
            for raw in client.lrange(key, 0, size - 1) if size else ():
                if len(raw) > 131072:
                    return None
                payload = json.loads(raw)
                task_id = (payload.get("headers") or {}).get("id")
                if isinstance(task_id, str):
                    ids.add(task_id)
        # Redis removes prefetched deliveries from the queue list. Kombu's
        # unacked hash retains their message envelope until acknowledgement.
        for raw in client.hvals("unacked") if unacked_count else ():
            if len(raw) > 131072:
                return None
            envelope = json.loads(raw)
            task_id = (envelope[0].get("headers") or {}).get("id")
            if isinstance(task_id, str):
                ids.add(task_id)
        return ids
    except Exception:
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def claim_publication(db, *, run_id, now, broker_ids):
    # Serialize only the short admission transaction, never broker I/O.
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(302, 97)")):
        return None
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id).with_for_update()
                    .execution_options(populate_existing=True))
    job = db.scalar(select(AIWorkflowDispatch).where(AIWorkflowDispatch.run_id == run_id)
                    .with_for_update().execution_options(populate_existing=True))
    if run is None or job is None or run.finished_at is not None or run.status != "queued":
        return None
    if (run.metadata_json or {}).get("cancel_requested_at") or job.state in {"running", "complete"}:
        return None
    if as_utc(job.next_attempt_at) > now:
        return None
    if job.claim_token and job.claim_expires_at and as_utc(job.claim_expires_at) > now:
        return None
    if job.delivery_id and job.attempt_count:
        if broker_ids is None or job.delivery_id in broker_ids:
            job.next_attempt_at = now + timedelta(seconds=60)
            job.error = "queue_state_unavailable" if broker_ids is None else "waiting_in_queue"
            db.add(job)
            return None
    if job.attempt_count == 0:
        outstanding = db.scalar(select(func.count()).select_from(AIWorkflowDispatch).join(
            AITaskRun, AITaskRun.id == AIWorkflowDispatch.run_id
        ).where(AITaskRun.status == "queued", AIWorkflowDispatch.attempt_count > 0,
                AIWorkflowDispatch.state != "complete"))
        if outstanding >= WORKFLOW_MAX_OUTSTANDING_PUBLICATIONS:
            job.next_attempt_at = now + timedelta(seconds=30)
            job.error = "queue_admission_wait"
            db.add(job)
            return None
    job.delivery_id = job.delivery_id or str(uuid.uuid4())
    run.celery_task_id = job.delivery_id
    job.claim_token = uuid.uuid4().hex
    job.claim_expires_at = now + timedelta(seconds=WORKFLOW_CLAIM_SECONDS)
    job.next_attempt_at = job.claim_expires_at
    job.state = "publishing"
    job.attempt_count = min(2_147_483_647, job.attempt_count + 1)
    db.add(run)
    db.add(job)
    return job.task_name, dict(job.payload_json), job.delivery_id, job.claim_token


def settle_publication(db, *, run_id, claim_token, now, error):
    # Match the execution lock order, and do not overwrite a worker's start.
    db.scalar(select(AITaskRun.id).where(AITaskRun.id == run_id).with_for_update())
    job = db.scalar(select(AIWorkflowDispatch).where(AIWorkflowDispatch.run_id == run_id)
                    .with_for_update().execution_options(populate_existing=True))
    if job is None or job.claim_token != claim_token or job.state != "publishing":
        return
    job.claim_token = None
    job.claim_expires_at = None
    job.state = "pending" if error else "published"
    job.error = error
    delay = min(900, 15 * 2 ** min(job.attempt_count - 1, 6)) if error else WORKFLOW_REPUBLISH_SECONDS
    job.next_attempt_at = now + timedelta(seconds=delay)
    if not error:
        job.published_at = now
    db.add(job)


def publish_ai_workflow(run_id, *, session_factory=None, publisher=None, broker_ids=BROKER_UNCHECKED):
    from app.tasks.celery_app import QUEUE_AI, celery_app
    from app.tasks.task_session import db_session
    session_factory = session_factory or db_session
    broker_ids = queued_ai_delivery_ids() if broker_ids is BROKER_UNCHECKED else broker_ids
    with session_factory() as db:
        claim = claim_publication(db, run_id=run_id, now=datetime.now(timezone.utc), broker_ids=broker_ids)
        db.commit()
        if claim is None:
            job = db.get(AIWorkflowDispatch, run_id)
            return SimpleNamespace(id=job.delivery_id if job else None)
    task_name, payload, delivery_id, claim_token = claim
    error = None
    try:
        (publisher or celery_app.send_task)(task_name, kwargs=payload, task_id=delivery_id, queue=QUEUE_AI)
    except Exception:
        error = "broker_publication_unavailable"
    with session_factory() as db:
        settle_publication(db, run_id=run_id, claim_token=claim_token,
                           now=datetime.now(timezone.utc), error=error)
        db.commit()
    return SimpleNamespace(id=delivery_id)


def dispatch_due_ai_workflows(*, session_factory=None, publisher=None, broker_ids=BROKER_UNCHECKED):
    from app.tasks.task_session import db_session
    from app.services.ai_workflow_recovery import adopt_legacy_workflows
    session_factory = session_factory or db_session
    with session_factory() as db:
        adopt_legacy_workflows(db, limit=WORKFLOW_BATCH_SIZE)
        ids = list(db.scalars(select(AIWorkflowDispatch.run_id).join(
            AITaskRun, AITaskRun.id == AIWorkflowDispatch.run_id
        ).where(
            AITaskRun.status == "queued", AITaskRun.finished_at.is_(None),
            AIWorkflowDispatch.state.in_(["pending", "publishing", "published"]),
            AIWorkflowDispatch.next_attempt_at <= datetime.now(timezone.utc),
        ).order_by(AIWorkflowDispatch.next_attempt_at, AIWorkflowDispatch.run_id).limit(WORKFLOW_BATCH_SIZE)))
        db.commit()
    broker_ids = queued_ai_delivery_ids() if broker_ids is BROKER_UNCHECKED else broker_ids
    for run_id in ids:
        publish_ai_workflow(run_id, session_factory=session_factory, publisher=publisher, broker_ids=broker_ids)
    return {"considered": len(ids)}

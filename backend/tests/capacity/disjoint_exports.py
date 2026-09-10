"""Two real durable export workers with distinct principals and source sets.

The mixed harness shares a finite connection pool across logical processes;
container/prefork isolation is verified separately by the deployment smoke test.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.export_job import ExportJob
from app.models.user import User
from app.schemas.exports import ArticleExportFilters, ArticleExportJobRequest, ArticleExportOptions
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization
from app.services.export_job_worker import execute_export_job
from app.services.export_jobs import create_export_job
from app.services.secret_storage import decrypt_json


def seed_export_principals(engine):
    principals = []
    with Session(engine) as db:
        for lane in range(2):
            owner = User(email=f"capacity-export-{uuid.uuid4()}@example.invalid", password_hash="synthetic-unused",
                         role="viewer", is_active=True, is_approved=True)
            db.add(owner)
            db.flush()
            _raw, prefix, digest = generate_api_token()
            token = ApiToken(user_id=owner.id, name=f"Synthetic export lane {lane}", token_prefix=prefix,
                             token_hash=digest, scopes=["read:items"],
                             expires_at=datetime.now(timezone.utc) + timedelta(hours=2))
            db.add(token)
            db.flush()
            principals.append((owner.id, token.id))
        db.commit()
    return principals


def run_disjoint_exports(engine, principals, feed_id, metrics, profile):
    barrier = threading.Barrier(2, timeout=30)

    def lane(index):
        owner_id, token_id = principals[index]
        with Session(engine) as db:
            owner = db.get(User, owner_id)
            authorization = authorization_context_for_user(db, owner, credential_scopes=["read:items"])
            request = Request({"type": "http"})
            request.state.auth_credential_kind = "api_token"
            request.state.api_token_id = token_id
            accepted = create_export_job(
                db, principal=owner, request=request, authorization=authorization,
                data_access=data_access_context_for_authorization(db, authorization),
                payload=ArticleExportJobRequest(
                    idempotency_key=str(uuid.uuid4()), format="jsonl",
                    filters=ArticleExportFilters(feed_ids=[feed_id], q=f"lane{index}"),
                    options=ArticleExportOptions(include_article_text=True, include_iocs=False),
                ),
            )
            job_id = accepted.job.id
            db.commit()
        barrier.wait()
        with metrics.operation("async_export") as operation:
            result = execute_export_job(job_id)
            with Session(engine) as db:
                job = db.get(ExportJob, job_id)
                assert result["status"] in {"ready", "failed"}, result
                if job.status == "failed":
                    assert job.error_code == "authorization_changed", job.error_code
                    operation["outcome"] = "policy_conflict"
                    return {"status": "policy_conflict", "source_ids": []}
                assert job.status == "ready"
                sources = decrypt_json(job.source_encrypted)
                ids = {row[0] for row in sources}
                expected = (profile["seed_items"] + (1 if index == 0 else 0)) // 2
                assert len(ids) == job.item_count == expected
                assert job.completed_items == expected
                assert job.file_size >= expected * profile["article_bytes"]
                operation["outcome"] = "succeeded"
                return {"status": "ready", "source_ids": ids, "items": expected, "bytes": job.file_size}

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(lane, index) for index in range(2)]
        results = [future.result(timeout=300) for future in futures]
    assert not set(results[0]["source_ids"]) & set(results[1]["source_ids"])
    return [{key: value for key, value in result.items() if key != "source_ids"} for result in results]

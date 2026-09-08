"""Status pages share bounded lookups without sharing authorization across requests."""

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, insert, select, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes.export_jobs import list_export_jobs
from app.api.routes import export_jobs as export_routes
from app.models.api_token import ApiToken
from app.models.data_policy import (
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
    DataPolicyState,
)
from app.models.export_job import ExportJob
from app.models.feed import Feed
from app.models.item import Item
from app.models.iam import IAMPolicyState
from app.models.user import User
from app.services import export_job_access, export_job_status
from app.services.export_job_access import authorize_export_job
from app.services.export_jobs import export_job_responses
from app.services.secret_storage import decrypt_json, encrypt_json
from tests.integration.test_export_jobs import _accept, export_env as export_env


def _ready_jobs(env, *, count=1, source_count=1):
    first_id, _payload = _accept(env)
    with Session(env.engine) as db:
        ids = [env.item_id, *(uuid.uuid4() for _ in range(source_count - 1))]
        if len(ids) > 1:
            db.execute(
                insert(Item),
                [
                    {
                        "id": identity,
                        "feed_id": env.feed_id,
                        "source_guid": str(identity),
                        "url": f"https://example.invalid/status/{identity}",
                        "canonical_url": f"https://example.invalid/status/{identity}",
                        "title": "Status source",
                        "dedupe_key": str(identity),
                        "content_hash": "a" * 64,
                    }
                    for identity in ids[1:]
                ],
            )
        source = [
            [str(identity), str(env.feed_id), str(UNRESTRICTED_HANDLING_LABEL_ID)]
            for identity in ids
        ]
        first = db.get(ExportJob, first_id)
        jobs = [first]
        for _ in range(count - 1):
            job = ExportJob(
                principal_type=first.principal_type,
                principal_id=first.principal_id,
                idempotency_key=uuid.uuid4(),
                request_hash=first.request_hash,
                request_encrypted=first.request_encrypted,
                authorization_encrypted=first.authorization_encrypted,
                format=first.format,
                expires_at=first.expires_at,
                reserved_bytes=100_000,
            )
            db.add(job)
            jobs.append(job)
        for job in jobs:
            job.status = "ready"
            job.source_encrypted = encrypt_json(source)
            job.item_count = source_count
            job.completed_items = source_count
            job.file_size = 12345
            job.filename = "snapshot.jsonl"
            job.media_type = "application/x-ndjson"
        db.commit()
        return [job.id for job in jobs]


def _contexts(db, job):
    authorization, access = authorize_export_job(db, job)
    request = Request(
        {
            "type": "http",
            "state": {"authorization_context": authorization},
            "headers": [],
        }
    )
    return request, authorization, access


@pytest.mark.parametrize("policy_model", [IAMPolicyState, DataPolicyState])
@pytest.mark.parametrize("action", ["accept", "cancel"])
def test_status_policy_revision_races_return_retryable_conflict(
    export_env, monkeypatch, policy_model, action
):
    env = export_env
    job_id, payload = _accept(env)
    if action == "accept":
        payload = {**payload, "idempotency_key": str(uuid.uuid4())}
    original = export_routes.export_job_responses

    def change_policy_before_status(db, jobs, **kwargs):
        # Mutation commit released the dependency's policy fences. A separate
        # transaction changes the revision before status serialization.
        with Session(env.engine) as other:
            other.execute(text("SET LOCAL lock_timeout = '2s'"))
            other.get(policy_model, 1).revision += 1
            other.commit()
        return original(db, jobs, **kwargs)

    monkeypatch.setattr(
        export_routes, "export_job_responses", change_policy_before_status
    )

    def request():
        if action == "accept":
            return env.client.post("/exports/jobs", json=payload, headers=env.headers)
        return env.client.post(f"/exports/jobs/{job_id}/cancel", headers=env.headers)

    response = request()
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "export_authorization_changed"
    assert "filename" not in response.json()
    assert "Retry this request" in response.json()["detail"]
    # Status failure must not lose a successful acceptance/cancellation or
    # create duplicates when the caller retries with its idempotency key.
    with Session(env.engine) as db:
        jobs = db.scalars(
            select(ExportJob).where(ExportJob.principal_id == env.owner_id)
        ).all()
        assert len(jobs) == (2 if action == "accept" else 1)
        if action == "cancel":
            assert jobs[0].status == "cancelled"
    monkeypatch.setattr(export_routes, "export_job_responses", original)
    retry = request()
    assert retry.status_code == (202 if action == "accept" else 200), retry.text


def test_large_overlapping_status_page_bounds_membership_queries(export_env):
    env = export_env
    ids = _ready_jobs(env, count=25, source_count=10_000)
    statements = []

    def count(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with Session(env.engine) as db:
        request, _authorization, access = _contexts(db, db.get(ExportJob, ids[0]))
        owner = db.get(User, env.owner_id)
        event.listen(env.engine, "before_cursor_execute", count)
        started = time.perf_counter()
        try:
            result = list_export_jobs(
                request, limit=25, offset=0, db=db, principal=owner, data_access=access
            )
        finally:
            event.remove(env.engine, "before_cursor_execute", count)
        elapsed = time.perf_counter() - started
        assert len(result.items) == 25
        assert all(
            job.download_available and job.item_count == 10_000 for job in result.items
        )
        # The cache contains identifiers and current labels, never Item/Article
        # entities, article text, or all decoded job snapshots at once.
        assert not any(isinstance(value, Item) for value in db.identity_map.values())
    membership = [
        statement for statement in statements if "FROM items JOIN feeds" in statement
    ]
    print(
        {
            "route_body_queries": len(statements),
            "membership_queries": len(membership),
            "seconds": elapsed,
        }
    )
    assert len(membership) == 20
    assert len(statements) <= 100


def test_batched_accepting_scopes_and_captured_labels_do_not_widen_each_other(
    export_env,
):
    from dataclasses import replace

    env = export_env
    ids = _ready_jobs(env, count=3)
    with Session(env.engine) as db:
        jobs = [db.get(ExportJob, identity) for identity in ids]
        request, authorization, access = _contexts(db, jobs[0])
        access = replace(
            access,
            mode="enforced",
            allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
        )
        narrower = decrypt_json(jobs[1].authorization_encrypted)
        narrower.update(enforced=True, allowed_label_ids=[])
        jobs[1].authorization_encrypted = encrypt_json(narrower)
        jobs[2].source_encrypted = encrypt_json(
            [[str(env.item_id), str(env.feed_id), str(QUARANTINE_HANDLING_LABEL_ID)]]
        )
        db.commit()
        result = export_job_responses(
            db, jobs, current_authorization=authorization, current_access=access
        )
        assert [job.download_available for job in result] == [True, False, False]
        assert [job.filename for job in result] == ["snapshot.jsonl", None, None]
        assert all(job.error_code == "authorization_changed" for job in result[1:])


def test_membership_changes_and_credential_revocation_are_rechecked_next_request(
    export_env,
):
    env = export_env
    ids = _ready_jobs(env, count=2)
    with Session(env.engine) as db:
        jobs = [db.get(ExportJob, identity) for identity in ids]
        _request, authorization, access = _contexts(db, jobs[0])
        assert all(
            job.download_available
            for job in export_job_responses(
                db,
                jobs,
                current_authorization=authorization,
                current_access=access,
            )
        )
    with Session(env.engine) as db:
        db.delete(db.get(Item, env.item_id))
        db.commit()
    with Session(env.engine) as db:
        jobs = [db.get(ExportJob, identity) for identity in ids]
        results = export_job_responses(
            db, jobs, current_authorization=authorization, current_access=access
        )
        assert all(
            not job.download_available and job.filename is None for job in results
        )
    with Session(env.engine) as db:
        db.get(ApiToken, env.credential_id).revoked_at = datetime.now(timezone.utc)
        db.commit()
    with Session(env.engine) as db:
        jobs = [db.get(ExportJob, identity) for identity in ids]
        results = export_job_responses(
            db, jobs, current_authorization=authorization, current_access=access
        )
        assert all(job.error_code == "authorization_changed" for job in results)


def test_expiry_during_membership_work_is_observed_before_metadata_is_returned(
    export_env, monkeypatch
):
    env = export_env
    ids = _ready_jobs(env, count=2)
    clock = [datetime.now(timezone.utc)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0] if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(export_job_access, "datetime", Clock)
    original = export_job_status.load_export_sources

    def advance_after_sources(job):
        sources = original(job)
        clock[0] += timedelta(seconds=2)
        return sources

    with Session(env.engine) as db:
        db.get(ApiToken, env.credential_id).expires_at = clock[0] + timedelta(seconds=1)
        db.commit()
        jobs = [db.get(ExportJob, identity) for identity in ids]
        _request, authorization, access = _contexts(db, jobs[0])
        monkeypatch.setattr(
            export_job_status, "load_export_sources", advance_after_sources
        )
        results = export_job_responses(
            db, jobs, current_authorization=authorization, current_access=access
        )
        assert all(
            not job.download_available and job.filename is None for job in results
        )


def test_membership_cache_stays_bounded_and_handles_disjoint_batches(
    export_env, monkeypatch
):
    env = export_env
    ids = _ready_jobs(env, source_count=20)
    monkeypatch.setattr(export_job_status, "MAX_CACHED_SOURCE_IDENTITIES", 6)
    monkeypatch.setattr(export_job_status, "SOURCE_LOOKUP_BATCH_SIZE", 3)
    with Session(env.engine) as db:
        job = db.get(ExportJob, ids[0])
        _authorization, access = authorize_export_job(db, job)
        sources = export_job_access.load_export_sources(job)
        cache = export_job_status._SourceMembership(db)
        assert cache.allows(sources, [access])
        assert len(cache.rows) <= 6
        assert cache.allows(list(reversed(sources)), [access])
        assert len(cache.rows) <= 6


def test_current_feed_labels_and_moved_sources_remain_ineligible(export_env):
    from dataclasses import replace

    env = export_env
    ids = _ready_jobs(env, count=2)
    with Session(env.engine) as db:
        jobs = [db.get(ExportJob, identity) for identity in ids]
        _request, authorization, access = _contexts(db, jobs[0])
        access = replace(
            access,
            mode="enforced",
            allowed_label_ids=frozenset({UNRESTRICTED_HANDLING_LABEL_ID}),
        )
        db.get(Feed, env.feed_id).handling_label_id = QUARANTINE_HANDLING_LABEL_ID
        db.commit()
        assert all(
            not result.download_available
            for result in export_job_responses(
                db,
                jobs,
                current_authorization=authorization,
                current_access=access,
            )
        )
        db.commit()
        db.get(Feed, env.feed_id).handling_label_id = UNRESTRICTED_HANDLING_LABEL_ID
        destination = Feed(
            name="Moved export source",
            url=f"https://example.invalid/{uuid.uuid4()}",
            handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        )
        db.add(destination)
        db.flush()
        db.get(Item, env.item_id).feed_id = destination.id
        db.commit()
        try:
            assert all(
                not result.download_available
                for result in export_job_responses(
                    db,
                    jobs,
                    current_authorization=authorization,
                    current_access=access,
                )
            )
        finally:
            db.commit()
            db.get(Item, env.item_id).feed_id = env.feed_id
            db.flush()
            db.delete(destination)
            db.commit()

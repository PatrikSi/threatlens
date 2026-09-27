"""Selected publication evidence and its revisions form one authorized snapshot."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Event
import time
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.security import generate_api_token
from app.models.api_token import ApiToken
from app.models.feed import Feed
from app.models.iam import IAMGroup, IAMGroupMembership
from app.models.integration import IntegrationEvent
from app.models.intel_assessment import IndicatorAssessment, IndicatorAssessmentLabel, ItemIntelState
from app.models.ioc import IOC
from app.models.item import Item
from app.models.team import Team
from app.models.user import User
from app.services import indicator_assessments
from app.services.authorization import authorization_context_for_user
from app.services.data_access_policy import data_access_context_for_authorization
from app.services.export_job_contracts import ExportAuthorizationSnapshot
from app.services.intel_events import emit_intel_events
from app.services.ioc_extraction import extract_iocs
from app.services.ioc_storage import replace_item_iocs
from app.services.team_assessment_access import AssessmentRequest


SCOPES = ["read:items", "read:teams"]


@dataclass(frozen=True)
class EvidenceFixture:
    user_id: uuid.UUID
    credential_id: uuid.UUID
    team_id: uuid.UUID
    item_id: uuid.UUID
    ioc_id: uuid.UUID
    value: str
    source_revision: int
    extraction_revision: int


def _extract(db: Session, item_id: uuid.UUID, content: str) -> None:
    replace_item_iocs(
        db,
        item_id=item_id,
        extracted=extract_iocs(title=content, summary=None, article_text=None),
    )
    emit_intel_events(db, item_id=item_id, deterministic=True)


@pytest.fixture()
def evidence_fixture(database_engine):
    user_id, credential_id, group_id, team_id, feed_id, item_id = [uuid.uuid4() for _ in range(6)]
    value = f"ioc-{item_id.hex}.net"
    _, prefix, digest = generate_api_token()
    with Session(database_engine) as db:
        db.add_all([
            User(
                id=user_id, email=f"evidence-{user_id}@example.test", password_hash="x",
                role="analyst", is_active=True, is_approved=True,
            ),
            IAMGroup(id=group_id, key=f"evidence-{group_id.hex}", name="Evidence readers"),
            Feed(id=feed_id, name="Evidence source", url=f"https://example.test/{feed_id}"),
        ])
        db.flush()
        item = Item(
            id=item_id, feed_id=feed_id, url=f"https://example.test/{item_id}",
            title="Source", dedupe_key=str(item_id), content_hash="a" * 64,
        )
        db.add_all([
            item,
            Team(id=team_id, key=f"evidence-{team_id.hex}", name="Evidence readers", membership_group_id=group_id),
            IAMGroupMembership(group_id=group_id, user_id=user_id),
            ApiToken(
                id=credential_id, user_id=user_id, name="Read-only evidence", token_prefix=prefix,
                token_hash=digest, scopes=SCOPES, expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        ])
        db.flush()
        _extract(db, item_id, f"Original observation: {value}.")
        ioc_id = db.scalar(select(IOC.id).where(IOC.value_norm == value))
        state = db.get(ItemIntelState, item_id)
        assessment = IndicatorAssessment(
            team_id=team_id, item_id=item_id, ioc_id=ioc_id,
            handling_label_id=db.get(Feed, feed_id).handling_label_id,
            source_revision=item.classification_required_version, extraction_revision=state.revision,
            verdict="malicious", reason="Reviewed the original passage.",
        )
        db.add(assessment)
        db.flush()
        db.add(IndicatorAssessmentLabel(
            assessment_id=assessment.id, handling_label_id=assessment.handling_label_id,
        ))
        fixture = EvidenceFixture(
            user_id, credential_id, team_id, item_id, ioc_id, value,
            item.classification_required_version, state.revision,
        )
        db.commit()
    try:
        yield fixture
    finally:
        with Session(database_engine) as db:
            db.execute(delete(IntegrationEvent).where(IntegrationEvent.source_id == str(item_id)))
            db.execute(delete(Team).where(Team.id == team_id))
            db.execute(delete(IAMGroup).where(IAMGroup.id == group_id))
            db.execute(delete(Feed).where(Feed.id == feed_id))
            db.execute(delete(IOC).where(IOC.id == ioc_id))
            db.execute(delete(User).where(User.id == user_id))
            db.commit()


def _actor(db: Session, fixture: EvidenceFixture) -> AssessmentRequest:
    user = db.get(User, fixture.user_id)
    authorization = authorization_context_for_user(db, user, credential_scopes=SCOPES)
    access = data_access_context_for_authorization(db, authorization)
    snapshot = ExportAuthorizationSnapshot(
        credential_kind="api_token", credential_id=fixture.credential_id,
        permissions=authorization.permissions, enforced=access.enforced,
        allowed_label_ids=access.allowed_label_ids,
    )
    return AssessmentRequest(user, authorization, access, snapshot)


def _page(db: Session, fixture: EvidenceFixture):
    return indicator_assessments.list_indicators(
        db, actor=_actor(db, fixture), item_id=fixture.item_id, team_id=fixture.team_id,
        page=1, page_size=1, ioc_id=fixture.ioc_id,
    )


def _wait_for_block(db: Session, *, writer_pid: int, reader_pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if db.scalar(
            text("SELECT :reader = ANY(pg_blocking_pids(:writer))"),
            {"reader": reader_pid, "writer": writer_pid},
        ):
            return
        time.sleep(0.01)
    raise AssertionError("Evidence reader did not fence the concurrent publication")


@pytest.mark.parametrize("writer_kind", ["source_refresh", "ai_publication"])
def test_selected_evidence_keeps_source_and_intel_revision_until_assembly_finishes(
    database_engine, evidence_fixture, monkeypatch, writer_kind,
):
    fixture = evidence_fixture
    revisions_read, resume_reader = Event(), Event()
    reader_pid, writer_pid = Queue(), Queue()
    original_current = indicator_assessments.extraction_is_current

    def pause_after_current(db, item):
        current = original_current(db, item)
        revisions_read.set()
        assert resume_reader.wait(10)
        return current

    monkeypatch.setattr(indicator_assessments, "extraction_is_current", pause_after_current)

    def read():
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            reader_pid.put(db.scalar(text("SELECT pg_backend_pid()")))
            result = _page(db, fixture)
            db.commit()
            return result

    def write():
        with Session(database_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            writer_pid.put(db.scalar(text("SELECT pg_backend_pid()")))
            if writer_kind == "source_refresh":
                item = db.scalar(select(Item).where(Item.id == fixture.item_id).with_for_update())
                item.classification_required_version += 1
                _extract(db, item.id, f"Refreshed observation: {fixture.value}.")
            else:
                # AI publication serializes through intel state without taking
                # a new Item lock after the worker's enrichment/execution locks.
                state = db.scalar(select(ItemIntelState).where(
                    ItemIntelState.item_id == fixture.item_id,
                ).with_for_update())
                state.revision += 1
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(read)
        try:
            assert revisions_read.wait(10)
            writing = pool.submit(write)
            with Session(database_engine) as observer:
                _wait_for_block(
                    observer, writer_pid=writer_pid.get(timeout=5), reader_pid=reader_pid.get(timeout=5),
                )
            resume_reader.set()
            original = reading.result(timeout=10)
            writing.result(timeout=10)
        finally:
            resume_reader.set()

    assert original.source_revision == fixture.source_revision
    assert original.extraction_revision == fixture.extraction_revision
    assert original.extraction_current is True
    assert original.items[0].assessment.current is True
    assert original.items[0].evidence[0].quote == f"Original observation: {fixture.value}."
    # The exact same read-only credential sees the committed replacement as a
    # new revision; its previous malicious assessment must no longer be current.
    with Session(database_engine) as db:
        refreshed = _page(db, fixture)
    assert refreshed.extraction_revision == fixture.extraction_revision + 1
    assert refreshed.items[0].assessment.current is False
    if writer_kind == "source_refresh":
        assert refreshed.source_revision == fixture.source_revision + 1
        assert refreshed.items[0].evidence[0].quote == f"Refreshed observation: {fixture.value}."


def test_selected_evidence_refreshes_cached_intel_state_after_a_new_commit(database_engine, evidence_fixture):
    fixture = evidence_fixture
    with Session(database_engine) as reader:
        cached = reader.get(ItemIntelState, fixture.item_id)
        assert cached.revision == fixture.extraction_revision
        with Session(database_engine) as writer:
            state = writer.scalar(select(ItemIntelState).where(
                ItemIntelState.item_id == fixture.item_id,
            ).with_for_update())
            state.revision += 1
            writer.commit()
        current = _page(reader, fixture)
        assert current.extraction_revision == fixture.extraction_revision + 1
        assert cached.revision == current.extraction_revision
        assert current.items[0].assessment.current is False

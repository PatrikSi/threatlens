import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app.api.routes import tags as tags_routes
from app.models.tag import Tag
from app.schemas.tag import TagCreate


def test_create_tag_handles_concurrent_normalized_name_race(database_engine, monkeypatch):
    normalized_name = f"race-tag-{uuid.uuid4().hex}"
    insert_barrier = threading.Barrier(2)
    actor = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(tags_routes, "record_audit", lambda *_args, **_kwargs: None)

    def create_racing_tag(name: str) -> tuple[int, str]:
        with Session(bind=database_engine, autoflush=False) as session:

            @event.listens_for(session, "before_flush", once=True)
            def synchronize_inserts(*_args):
                insert_barrier.wait(timeout=10)

            try:
                tag = tags_routes.create_tag(
                    TagCreate(name=name),
                    db=session,
                    user=actor,
                )
            except HTTPException as exc:
                return exc.status_code, str(exc.detail)
            return 201, tag.name

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(create_racing_tag, normalized_name.upper()),
                executor.submit(create_racing_tag, normalized_name),
            ]
            results = [future.result(timeout=15) for future in futures]

        assert sorted(status_code for status_code, _detail in results) == [201, 400]
        assert (400, "Tag already exists") in results
        assert (201, normalized_name) in results

        with Session(bind=database_engine) as verification_session:
            tags = verification_session.scalars(select(Tag).where(Tag.name == normalized_name)).all()
            assert len(tags) == 1
    finally:
        with database_engine.begin() as connection:
            connection.execute(delete(Tag).where(Tag.name == normalized_name))

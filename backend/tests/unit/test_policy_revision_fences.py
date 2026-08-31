from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.data_policy import DataPolicyState
from app.models.iam import IAMPolicyState
from app.services.authorization import (
    AuthorizationStateUnavailable,
    fence_authorization_context,
)
from app.services.data_access_policy import (
    DataPolicyRevisionConflict,
    fence_data_access_context,
)


@pytest.mark.parametrize(
    ("model", "fence", "error_type"),
    [
        (IAMPolicyState, fence_authorization_context, AuthorizationStateUnavailable),
        (DataPolicyState, fence_data_access_context, DataPolicyRevisionConflict),
    ],
)
def test_policy_revision_fence_blocks_mutation_and_rejects_stale_context(
    database_engine,
    model: type[IAMPolicyState] | type[DataPolicyState],
    fence: Callable[[Session, Any], None],
    error_type: type[Exception],
):
    application_name = f"threatlens-fence-test-{uuid.uuid4()}"
    writer_started = threading.Event()
    writer_finished = threading.Event()
    writer_errors: list[BaseException] = []

    with Session(database_engine) as request_db:
        state = request_db.get(model, 1)
        assert state is not None
        original_revision = state.revision
        context = SimpleNamespace(policy_revision=original_revision)
        fence(request_db, context)

        def mutate_revision() -> None:
            try:
                with Session(database_engine) as mutation_db:
                    mutation_db.execute(
                        select(
                            text("set_config('application_name', :name, false)")
                        ),
                        {"name": application_name},
                    )
                    writer_started.set()
                    locked_state = mutation_db.scalar(
                        select(model).where(model.id == 1).with_for_update()
                    )
                    assert locked_state is not None
                    locked_state.revision += 1
                    mutation_db.commit()
            except BaseException as exc:  # pragma: no cover - surfaced below
                writer_errors.append(exc)
            finally:
                writer_finished.set()

        writer = threading.Thread(target=mutate_revision, daemon=True)
        writer.start()
        assert writer_started.wait(timeout=2)

        deadline = time.monotonic() + 3
        writer_is_blocked = False
        with database_engine.connect() as observer:
            while time.monotonic() < deadline:
                writer_is_blocked = bool(
                    observer.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name = :name "
                            "AND wait_event_type = 'Lock'"
                            ")"
                        ),
                        {"name": application_name},
                    )
                )
                if writer_is_blocked:
                    break
                time.sleep(0.02)

        assert writer_is_blocked
        assert not writer_finished.is_set()
        request_db.commit()
        assert writer_finished.wait(timeout=3)
        writer.join(timeout=1)
        assert not writer_errors

    try:
        with Session(database_engine) as stale_db:
            with pytest.raises(error_type):
                fence(stale_db, context)
    finally:
        with Session(database_engine) as restore_db:
            restored_state = restore_db.scalar(
                select(model).where(model.id == 1).with_for_update()
            )
            assert restored_state is not None
            restored_state.revision = original_revision
            restore_db.commit()

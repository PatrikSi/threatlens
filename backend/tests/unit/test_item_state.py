import uuid

from sqlalchemy.exc import IntegrityError

from app.models.item_state import ItemState
from app.services.item_state import get_or_create_item_state


class _FakeSession:
    def __init__(self, existing_state: ItemState):
        self._scalar_results = [None, existing_state]
        self.added: list[ItemState] = []
        self.rollback_calls = 0
        self.flush_calls = 0

    def scalar(self, _query):
        if self._scalar_results:
            return self._scalar_results.pop(0)
        return None

    def add(self, state: ItemState):
        self.added.append(state)

    def flush(self):
        self.flush_calls += 1
        raise IntegrityError("insert", {}, RuntimeError("duplicate key"))

    def rollback(self):
        self.rollback_calls += 1


def test_get_or_create_state_recovers_from_duplicate_insert_race():
    user_id = uuid.uuid4()
    item_id = uuid.uuid4()
    existing_state = ItemState(user_id=user_id, item_id=item_id)
    db = _FakeSession(existing_state)

    state = get_or_create_item_state(db, user_id=user_id, item_id=item_id)

    assert state is existing_state
    assert db.flush_calls == 1
    assert db.rollback_calls == 1
    assert len(db.added) == 1

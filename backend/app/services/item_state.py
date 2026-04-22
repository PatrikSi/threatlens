import uuid

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.item_state import ItemState


def get_or_create_item_state(db: Session, *, user_id: uuid.UUID, item_id: uuid.UUID) -> ItemState:
    state = db.scalar(
        select(ItemState).where(
            and_(
                ItemState.user_id == user_id,
                ItemState.item_id == item_id,
            )
        )
    )
    if state is None:
        state = ItemState(user_id=user_id, item_id=item_id)
        db.add(state)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            state = db.scalar(
                select(ItemState).where(
                    and_(
                        ItemState.user_id == user_id,
                        ItemState.item_id == item_id,
                    )
                )
            )
            if state is None:
                raise
    return state

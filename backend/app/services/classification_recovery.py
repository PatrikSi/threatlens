"""Durable classification intent, committed together with its source mutation."""
from app.models.item import Item


def require_item_classification(item: Item) -> None:
    # Evaluate the increment in PostgreSQL: feed updates can have read the item
    # before a concurrent article fetch acquired its row lock. A Python += risks
    # overwriting the newer revision after waiting for that writer's commit.
    item.classification_required_version = Item.classification_required_version + 1

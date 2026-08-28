from __future__ import annotations

from sqlalchemy.exc import OperationalError

_LOCK_CONTENTION_SQLSTATES = frozenset({"40P01", "55P03"})


class WebhookDeliveryBusyError(ValueError):
    code = "webhook_delivery_busy"


def is_webhook_delivery_lock_contention(error: OperationalError) -> bool:
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original,
        "pgcode",
        None,
    )
    return sqlstate in _LOCK_CONTENTION_SQLSTATES

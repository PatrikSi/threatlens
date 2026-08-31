from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace

from app.services.notification_webhook_templates import AlertMatchContext
from app.services.smtp_alert_context import combine_smtp_alert_contexts


@dataclass(frozen=True)
class SMTPLegacyAlertSnapshot:
    item: SimpleNamespace
    feed: SimpleNamespace
    contexts_by_owner: dict[uuid.UUID, AlertMatchContext]

    @property
    def alert_context(self) -> AlertMatchContext:
        return combine_smtp_alert_contexts(self.contexts_by_owner)

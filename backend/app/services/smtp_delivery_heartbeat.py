from __future__ import annotations

from collections.abc import Callable

from app.services.integration_storage import ActiveSMTPSettings


def persisted_smtp_settings_heartbeat(
    heartbeat: Callable[[int, ActiveSMTPSettings], None],
    *,
    persisted_settings: ActiveSMTPSettings,
) -> Callable[[int, ActiveSMTPSettings], None]:
    def _heartbeat(lease_seconds: int, _effective_settings: ActiveSMTPSettings) -> None:
        heartbeat(lease_seconds, persisted_settings)

    return _heartbeat

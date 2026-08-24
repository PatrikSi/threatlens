from __future__ import annotations

from app.services.ai_config import ActiveAISettings


class ReportingUnavailableError(RuntimeError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def ensure_reporting_available(active: ActiveAISettings) -> None:
    if not active.ai_enabled:
        raise ReportingUnavailableError(
            "AI features are disabled by the server administrator.",
            code="ai_disabled",
        )
    if not active.ai_configured:
        raise ReportingUnavailableError(
            "Configure and test an AI provider before generating reports.",
            code="ai_not_configured",
        )
    if not active.reporting_enabled:
        raise ReportingUnavailableError(
            "AI reporting is disabled in AI settings.",
            code="reporting_disabled",
        )


__all__ = ["ReportingUnavailableError", "ensure_reporting_available"]

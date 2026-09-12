"""Explain the limits of bounded provider connection checks."""

from app.services.ai_config import ActiveAISettings
from app.services.ai_provider_client import AIIntegrationError


def connection_test_error(active: ActiveAISettings, error: AIIntegrationError) -> str:
    if active.provider_id is None or error.retry_hint != "expand_completion_budget":
        return str(error)
    return (
        "The AI endpoint responded, but the connection test exhausted its fixed "
        f"{active.max_completion_tokens:,}-token completion limit before returning valid JSON. "
        "Reasoning can consume this allowance. Increasing the saved default completion "
        "budget does not change this small diagnostic test. Feature compatibility remains "
        "unverified; check a small article or report request using the saved feature budget."
    )

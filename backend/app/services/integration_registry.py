from app.schemas.integration import IntegrationConnectorResponse
from app.services.integration_connectors.base import IntegrationConnector
from app.services.integration_connectors.smtp import SMTPIntegrationConnector
from app.services.integration_connectors.webhook import WebhookIntegrationConnector
from app.services.integration_registry_constants import SMTP_CONFIG_SCHEMA_VERSION

_CONNECTORS: dict[str, IntegrationConnector] = {}


def register_integration_connector(connector: IntegrationConnector) -> None:
    integration_type = connector.definition.integration_type
    if not integration_type:
        raise ValueError("Integration connector type cannot be empty")
    if integration_type in _CONNECTORS:
        raise ValueError(f"Integration connector already registered: {integration_type}")
    _CONNECTORS[integration_type] = connector


def get_integration_connector(integration_type: str) -> IntegrationConnector | None:
    return _CONNECTORS.get(integration_type)


def iter_integration_connectors() -> tuple[IntegrationConnector, ...]:
    return tuple(_CONNECTORS.values())


def list_integration_connectors() -> list[IntegrationConnectorResponse]:
    return [connector.definition.to_response() for connector in iter_integration_connectors()]


register_integration_connector(SMTPIntegrationConnector())
register_integration_connector(WebhookIntegrationConnector())

__all__ = [
    "SMTP_CONFIG_SCHEMA_VERSION",
    "get_integration_connector",
    "iter_integration_connectors",
    "list_integration_connectors",
    "register_integration_connector",
]

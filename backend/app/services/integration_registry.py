from app.schemas.integration import IntegrationConnectorResponse
from app.services.integration_connectors.base import IntegrationConnector
from app.services.integration_connectors.smtp import SMTPIntegrationConnector
from app.services.integration_connectors.webhook import WebhookIntegrationConnector
from app.services.integration_registry_constants import SMTP_CONFIG_SCHEMA_VERSION

_CONNECTORS: tuple[IntegrationConnector, ...] = (
    SMTPIntegrationConnector(),
    WebhookIntegrationConnector(),
)
_CONNECTORS_BY_TYPE = {connector.definition.integration_type: connector for connector in _CONNECTORS}


def get_integration_connector(integration_type: str) -> IntegrationConnector | None:
    return _CONNECTORS_BY_TYPE.get(integration_type)


def iter_integration_connectors() -> tuple[IntegrationConnector, ...]:
    return _CONNECTORS


def list_integration_connectors() -> list[IntegrationConnectorResponse]:
    return [connector.definition.to_response() for connector in iter_integration_connectors()]


__all__ = [
    "SMTP_CONFIG_SCHEMA_VERSION",
    "get_integration_connector",
    "iter_integration_connectors",
    "list_integration_connectors",
]

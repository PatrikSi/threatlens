from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import IntegrationInstance, IntegrationSubscription
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


def iter_integration_connectors_for_event(event_type: str) -> tuple[IntegrationConnector, ...]:
    return tuple(connector for connector in _CONNECTORS if connector.supports_event_type(event_type))


def list_subscription_connector_types(db: Session, *, event_type: str) -> tuple[str, ...]:
    """Return connector types with an active persisted subscription for an event."""
    return tuple(
        db.scalars(
            select(IntegrationInstance.integration_type)
            .join(
                IntegrationSubscription,
                IntegrationSubscription.integration_id == IntegrationInstance.id,
            )
            .where(
                IntegrationInstance.enabled.is_(True),
                IntegrationSubscription.enabled.is_(True),
                IntegrationSubscription.event_type == event_type,
            )
            .distinct()
            .order_by(IntegrationInstance.integration_type.asc())
        ).all()
    )


def list_integration_connectors() -> list[IntegrationConnectorResponse]:
    return [connector.definition.to_response() for connector in iter_integration_connectors()]


__all__ = [
    "SMTP_CONFIG_SCHEMA_VERSION",
    "get_integration_connector",
    "iter_integration_connectors",
    "iter_integration_connectors_for_event",
    "list_subscription_connector_types",
    "list_integration_connectors",
]

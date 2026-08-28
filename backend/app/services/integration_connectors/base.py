from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.models.integration import IntegrationDelivery, IntegrationEvent
from app.schemas.integration import IntegrationConnectorResponse


class IntegrationEventContextError(ValueError):
    pass


class IntegrationEventCompatibilityError(RuntimeError):
    """A newer persisted event needs a compatible connector worker."""


@dataclass(frozen=True)
class IntegrationConnectorDefinition:
    integration_type: str
    direction: str
    display_name: str
    description: str
    config_schema_version: int
    supports_test: bool
    handles_delivery_compatibility: bool = False
    supported_event_types: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def to_response(self) -> IntegrationConnectorResponse:
        return IntegrationConnectorResponse(
            integration_type=self.integration_type,
            direction=self.direction,
            display_name=self.display_name,
            description=self.description,
            config_schema_version=self.config_schema_version,
            supports_test=self.supports_test,
            capabilities=list(self.capabilities),
        )


@dataclass(frozen=True)
class ConnectorRoutingResult:
    delivery_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)
    compatibility_delivery_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ConnectorFollowupDelivery:
    delivery_id: uuid.UUID
    countdown_seconds: int | None = None


@dataclass(frozen=True)
class ConnectorDeliveryResult:
    delivery_id: uuid.UUID
    status: str
    reason: str | None = None
    retry_at: str | None = None
    followup_deliveries: tuple[ConnectorFollowupDelivery, ...] = field(
        default_factory=tuple
    )
    followup_event_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)


@runtime_checkable
class IntegrationConnector(Protocol):
    definition: IntegrationConnectorDefinition

    def supports_event_type(self, event_type: str) -> bool: ...

    def prepare_routing(self, db: Session, *, event: IntegrationEvent) -> None: ...

    def route_event(
        self, db: Session, *, event: IntegrationEvent
    ) -> ConnectorRoutingResult: ...

    def process_delivery(
        self,
        db: Session,
        *,
        delivery: IntegrationDelivery,
    ) -> ConnectorDeliveryResult: ...

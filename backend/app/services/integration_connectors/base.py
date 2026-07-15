from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.models.integration import IntegrationDelivery, IntegrationEvent
from app.schemas.integration import IntegrationConnectorResponse


class IntegrationEventContextError(ValueError):
    pass


@dataclass(frozen=True)
class IntegrationConnectorDefinition:
    integration_type: str
    direction: str
    display_name: str
    description: str
    config_schema_version: int
    supports_test: bool
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
    delivery_ids: list[uuid.UUID] = field(default_factory=list)
    compatibility_delivery_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass(frozen=True)
class ConnectorDeliveryResult:
    delivery_id: uuid.UUID
    status: str
    reason: str | None = None
    retry_at: str | None = None


@dataclass(frozen=True)
class IntegrationConnectorRuntime:
    enqueue_deliveries: Callable[[list[uuid.UUID], int | None], bool]
    enqueue_events: Callable[[list[uuid.UUID]], bool]


@runtime_checkable
class IntegrationConnector(Protocol):
    definition: IntegrationConnectorDefinition

    def route_event(self, db: Session, *, event: IntegrationEvent) -> ConnectorRoutingResult: ...

    def process_delivery(
        self,
        db: Session,
        *,
        delivery: IntegrationDelivery,
        runtime: IntegrationConnectorRuntime,
    ) -> ConnectorDeliveryResult: ...

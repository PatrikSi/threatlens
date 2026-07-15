import uuid

import pytest

from app.services import integration_registry
from app.services.integration_connectors import (
    ConnectorDeliveryResult,
    ConnectorRoutingResult,
    IntegrationConnector,
    IntegrationConnectorDefinition,
)


def test_builtin_connectors_are_executable_and_preserve_discovery_order():
    connectors = integration_registry.iter_integration_connectors()

    assert [connector.definition.integration_type for connector in connectors] == ["smtp", "webhook"]
    assert all(isinstance(connector, IntegrationConnector) for connector in connectors)
    assert [item.integration_type for item in integration_registry.list_integration_connectors()] == [
        "smtp",
        "webhook",
    ]
    assert integration_registry.get_integration_connector("smtp") is connectors[0]
    assert integration_registry.get_integration_connector("missing") is None


def test_registry_accepts_future_connector_types_and_rejects_duplicates(monkeypatch):
    monkeypatch.setattr(integration_registry, "_CONNECTORS", {})
    connector = _ExampleConnector()

    integration_registry.register_integration_connector(connector)

    assert integration_registry.get_integration_connector("example") is connector
    assert integration_registry.list_integration_connectors()[0].integration_type == "example"
    with pytest.raises(ValueError, match="already registered"):
        integration_registry.register_integration_connector(connector)


class _ExampleConnector:
    definition = IntegrationConnectorDefinition(
        integration_type="example",
        direction="destination",
        display_name="Example",
        description="Example connector used by the registry contract test.",
        config_schema_version=1,
        supports_test=False,
    )

    def route_event(self, db, *, event):
        return ConnectorRoutingResult()

    def process_delivery(self, db, *, delivery, runtime):
        return ConnectorDeliveryResult(uuid.uuid4(), "succeeded")

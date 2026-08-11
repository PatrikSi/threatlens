from app.services import integration_registry
from app.services.integration_connectors import (
    IntegrationConnector,
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
    assert all(connector.supports_event_type("rss_item_new") for connector in connectors)
    assert integration_registry.iter_integration_connectors_for_event("rss_item_new") == connectors
    assert integration_registry.iter_integration_connectors_for_event("future_event") == ()

from app.schemas.integration import IntegrationConnectorResponse

SMTP_CONFIG_SCHEMA_VERSION = 3


def list_integration_connectors() -> list[IntegrationConnectorResponse]:
    return [
        IntegrationConnectorResponse(
            integration_type="smtp",
            direction="destination",
            display_name="SMTP",
            description="Send operational emails through an SMTP server.",
            config_schema_version=SMTP_CONFIG_SCHEMA_VERSION,
            supports_test=True,
            capabilities=["destination", "email", "test_connection", "test_delivery"],
        ),
        IntegrationConnectorResponse(
            integration_type="webhook",
            direction="destination",
            display_name="Webhook",
            description="Deliver ThreatLens events to configurable HTTP endpoints.",
            config_schema_version=1,
            supports_test=True,
            capabilities=["destination", "http", "test_delivery", "retries", "delivery_history"],
        ),
    ]

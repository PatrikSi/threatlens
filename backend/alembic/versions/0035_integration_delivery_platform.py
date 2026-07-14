"""add generic integration event and delivery platform

Revision ID: 0035_integration_platform
Revises: 0034_integrations_smtp
Create Date: 2026-07-14
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa


revision = "0035_integration_platform"
down_revision = "0034_integrations_smtp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integration_instances", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.add_column(
        "integration_instances",
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "integration_instances",
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "integration_instances",
        sa.Column("circuit_state", sa.String(length=16), nullable=False, server_default="closed"),
    )
    op.add_column(
        "integration_instances",
        sa.Column("circuit_failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("integration_instances", sa.Column("circuit_opened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("integration_instances", sa.Column("circuit_open_until", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_integration_instances_owner_user_id",
        "integration_instances",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_integration_instances_owner_user_id",
        "integration_instances",
        ["owner_user_id"],
        unique=False,
    )

    op.create_table(
        "integration_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_key", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("filter_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("transform_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "integration_id",
            "subscription_key",
            name="uq_integration_subscriptions_instance_key",
        ),
    )
    op.create_index(
        "ix_integration_subscriptions_integration_id",
        "integration_subscriptions",
        ["integration_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_subscriptions_event_type",
        "integration_subscriptions",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_integration_subscriptions_event_enabled",
        "integration_subscriptions",
        ["event_type", "enabled"],
        unique=False,
    )

    op.create_table(
        "integration_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("routing_state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("routing_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_integration_events_idempotency_key"),
    )
    op.create_index("ix_integration_events_event_type", "integration_events", ["event_type"], unique=False)
    op.create_index("ix_integration_events_source_id", "integration_events", ["source_id"], unique=False)
    op.create_index("ix_integration_events_actor_user_id", "integration_events", ["actor_user_id"], unique=False)
    op.create_index(
        "ix_integration_events_routing_due",
        "integration_events",
        ["routing_state", "available_at", "created_at"],
        unique=False,
    )

    op.create_table(
        "integration_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("source_delivery_id", sa.Uuid(), nullable=True),
        sa.Column("connector_type", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("delivery_kind", sa.String(length=16), nullable=False, server_default="live"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_duration_ms", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_error_retryable", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["event_id"], ["integration_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_delivery_id"], ["integration_deliveries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subscription_id"], ["integration_subscriptions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_integration_deliveries_idempotency_key"),
    )
    for column in (
        "integration_id",
        "subscription_id",
        "event_id",
        "owner_user_id",
        "source_delivery_id",
        "connector_type",
        "event_type",
        "state",
        "not_before",
    ):
        op.create_index(f"ix_integration_deliveries_{column}", "integration_deliveries", [column], unique=False)
    op.create_index(
        "ix_integration_deliveries_state_due",
        "integration_deliveries",
        ["state", "not_before", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_integration_deliveries_instance_created",
        "integration_deliveries",
        ["integration_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_integration_deliveries_owner_created",
        "integration_deliveries",
        ["owner_user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "integration_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["delivery_id"], ["integration_deliveries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", "attempt_number", name="uq_integration_attempts_delivery_number"),
    )
    op.create_index("ix_integration_attempts_delivery_id", "integration_attempts", ["delivery_id"], unique=False)
    op.create_index("ix_integration_attempts_integration_id", "integration_attempts", ["integration_id"], unique=False)
    op.create_index("ix_integration_attempts_status", "integration_attempts", ["status"], unique=False)
    op.create_index(
        "ix_integration_attempts_instance_started",
        "integration_attempts",
        ["integration_id", "started_at"],
        unique=False,
    )

    op.add_column("notification_webhooks", sa.Column("integration_id", sa.Uuid(), nullable=True))
    op.add_column("notification_webhooks", sa.Column("subscription_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_notification_webhooks_integration_id",
        "notification_webhooks",
        "integration_instances",
        ["integration_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_notification_webhooks_subscription_id",
        "notification_webhooks",
        "integration_subscriptions",
        ["subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_notification_webhooks_integration_id", "notification_webhooks", ["integration_id"])
    op.create_unique_constraint("uq_notification_webhooks_subscription_id", "notification_webhooks", ["subscription_id"])

    op.add_column(
        "notification_webhook_deliveries",
        sa.Column("integration_delivery_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_notification_webhook_deliveries_integration_delivery_id",
        "notification_webhook_deliveries",
        "integration_deliveries",
        ["integration_delivery_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_notification_webhook_deliveries_integration_delivery_id",
        "notification_webhook_deliveries",
        ["integration_delivery_id"],
    )

    _backfill_webhook_integrations()
    _backfill_smtp_subscriptions()
    _backfill_webhook_deliveries()


def downgrade() -> None:
    op.drop_constraint(
        "uq_notification_webhook_deliveries_integration_delivery_id",
        "notification_webhook_deliveries",
        type_="unique",
    )
    op.drop_constraint(
        "fk_notification_webhook_deliveries_integration_delivery_id",
        "notification_webhook_deliveries",
        type_="foreignkey",
    )
    op.drop_column("notification_webhook_deliveries", "integration_delivery_id")

    op.drop_constraint("uq_notification_webhooks_subscription_id", "notification_webhooks", type_="unique")
    op.drop_constraint("uq_notification_webhooks_integration_id", "notification_webhooks", type_="unique")
    op.drop_constraint("fk_notification_webhooks_subscription_id", "notification_webhooks", type_="foreignkey")
    op.drop_constraint("fk_notification_webhooks_integration_id", "notification_webhooks", type_="foreignkey")
    op.drop_column("notification_webhooks", "subscription_id")
    op.drop_column("notification_webhooks", "integration_id")

    op.drop_table("integration_attempts")
    op.drop_table("integration_deliveries")
    op.drop_table("integration_events")
    op.drop_table("integration_subscriptions")

    op.execute("DELETE FROM integration_instances WHERE integration_type = 'webhook'")
    op.drop_index("ix_integration_instances_owner_user_id", table_name="integration_instances")
    op.drop_constraint("fk_integration_instances_owner_user_id", "integration_instances", type_="foreignkey")
    op.drop_column("integration_instances", "circuit_open_until")
    op.drop_column("integration_instances", "circuit_opened_at")
    op.drop_column("integration_instances", "circuit_failure_count")
    op.drop_column("integration_instances", "circuit_state")
    op.drop_column("integration_instances", "rate_limit_per_minute")
    op.drop_column("integration_instances", "max_concurrency")
    op.drop_column("integration_instances", "owner_user_id")


def _backfill_webhook_integrations() -> None:
    connection = op.get_bind()
    webhook_rows = connection.execute(
        sa.text(
            """
            SELECT id, user_id, name, enabled, event_type, feed_scope, feed_ids_json
            FROM notification_webhooks
            ORDER BY created_at ASC, id ASC
            """
        )
    ).mappings()

    for webhook in webhook_rows:
        integration_id = webhook["id"]
        if connection.execute(
            sa.text("SELECT 1 FROM integration_instances WHERE id = :id"),
            {"id": integration_id},
        ).scalar_one_or_none():
            integration_id = uuid.uuid4()

        connection.execute(
            sa.text(
                """
                INSERT INTO integration_instances (
                    id, owner_user_id, system_key, name, integration_type, direction, enabled,
                    schema_version, config_json, secret_json, health_status,
                    max_concurrency, rate_limit_per_minute, circuit_state, circuit_failure_count
                ) VALUES (
                    :id, :owner_user_id, NULL, :name, 'webhook', 'destination', :enabled,
                    1, :config_json, NULL, 'unknown', 2, 60, 'closed', 0
                )
                """
            ).bindparams(sa.bindparam("config_json", type_=sa.JSON())),
            {
                "id": integration_id,
                "owner_user_id": webhook["user_id"],
                "name": webhook["name"],
                "enabled": webhook["enabled"],
                "config_json": {"legacy_webhook_id": str(webhook["id"])},
            },
        )

        subscription_id = webhook["id"]
        if connection.execute(
            sa.text("SELECT 1 FROM integration_subscriptions WHERE id = :id"),
            {"id": subscription_id},
        ).scalar_one_or_none():
            subscription_id = uuid.uuid4()

        connection.execute(
            sa.text(
                """
                INSERT INTO integration_subscriptions (
                    id, integration_id, subscription_key, event_type, enabled, filter_json, transform_json
                ) VALUES (
                    :id, :integration_id, 'legacy-webhook', :event_type, :enabled, :filter_json, :transform_json
                )
                """
            ).bindparams(
                sa.bindparam("filter_json", type_=sa.JSON()),
                sa.bindparam("transform_json", type_=sa.JSON()),
            ),
            {
                "id": subscription_id,
                "integration_id": integration_id,
                "event_type": webhook["event_type"],
                "enabled": webhook["enabled"],
                "filter_json": {
                    "feed_scope": webhook["feed_scope"],
                    "feed_ids": webhook["feed_ids_json"] or [],
                },
                "transform_json": {"legacy_webhook_id": str(webhook["id"])},
            },
        )
        connection.execute(
            sa.text(
                """
                UPDATE notification_webhooks
                SET integration_id = :integration_id, subscription_id = :subscription_id
                WHERE id = :webhook_id
                """
            ),
            {
                "integration_id": integration_id,
                "subscription_id": subscription_id,
                "webhook_id": webhook["id"],
            },
        )


def _backfill_smtp_subscriptions() -> None:
    connection = op.get_bind()
    smtp_rows = connection.execute(
        sa.text("SELECT id, enabled, config_json FROM integration_instances WHERE integration_type = 'smtp'")
    ).mappings()

    for instance in smtp_rows:
        config = instance["config_json"] if isinstance(instance["config_json"], dict) else {}
        event_types = config.get("event_types") or ["rss_item_new"]
        filter_json = {
            "feed_scope": config.get("feed_scope") or "all",
            "feed_ids": config.get("feed_ids") or [],
        }
        for event_type in dict.fromkeys(str(value) for value in event_types if value):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO integration_subscriptions (
                        id, integration_id, subscription_key, event_type, enabled, filter_json, transform_json
                    ) VALUES (
                        :id, :integration_id, :subscription_key, :event_type, :enabled, :filter_json, '{}'
                    )
                    ON CONFLICT (integration_id, subscription_key) DO NOTHING
                    """
                ).bindparams(sa.bindparam("filter_json", type_=sa.JSON())),
                {
                    "id": uuid.uuid4(),
                    "integration_id": instance["id"],
                    "subscription_key": f"event:{event_type}",
                    "event_type": event_type,
                    "enabled": instance["enabled"],
                    "filter_json": filter_json,
                },
            )


def _backfill_webhook_deliveries() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO integration_deliveries (
                id, integration_id, subscription_id, event_id, owner_user_id, source_delivery_id,
                connector_type, event_type, delivery_kind, state, idempotency_key, payload_json,
                attempt_count, max_attempts, not_before, claimed_at, completed_at, dead_lettered_at,
                last_status_code, last_duration_ms, last_error_code, last_error_message,
                last_error_retryable, created_at, updated_at
            )
            SELECT
                d.id, w.integration_id, w.subscription_id, NULL, d.user_id, d.source_delivery_id,
                'webhook', d.event_type_snapshot, d.delivery_kind, d.delivery_state,
                'legacy-webhook-delivery:' || d.id::text,
                json_build_object('legacy_webhook_delivery_id', d.id::text),
                d.attempt_count, GREATEST(d.attempt_count, 3), d.not_before, d.claimed_at,
                CASE WHEN d.delivery_state IN ('succeeded', 'failed') THEN d.attempted_at ELSE NULL END,
                NULL, d.status_code, d.duration_ms, NULL, d.error, NULL,
                d.attempted_at, d.attempted_at
            FROM notification_webhook_deliveries d
            JOIN notification_webhooks w ON w.id = d.webhook_id
            WHERE w.integration_id IS NOT NULL
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE notification_webhook_deliveries
            SET integration_delivery_id = id
            WHERE EXISTS (SELECT 1 FROM integration_deliveries d WHERE d.id = notification_webhook_deliveries.id)
            """
        )
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO integration_attempts (
                id, delivery_id, integration_id, attempt_number, status, started_at, finished_at,
                duration_ms, status_code, error_code, error_message, retryable, response_json, created_at
            )
            SELECT
                d.id, d.id, w.integration_id, GREATEST(d.attempt_count, 1),
                CASE WHEN d.success THEN 'succeeded' ELSE 'failed' END,
                d.attempted_at - (COALESCE(d.duration_ms, 0) * interval '1 millisecond'),
                d.attempted_at, d.duration_ms, d.status_code, NULL, d.error, NULL, '{}', d.attempted_at
            FROM notification_webhook_deliveries d
            JOIN notification_webhooks w ON w.id = d.webhook_id
            WHERE w.integration_id IS NOT NULL
              AND d.delivery_state IN ('succeeded', 'failed')
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

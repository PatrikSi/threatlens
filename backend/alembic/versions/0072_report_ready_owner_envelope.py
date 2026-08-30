"""make report-ready ownership immutable and rolling-upgrade safe

Revision ID: 0072_report_ready_owner_envelope
Revises: 0071_data_access_lineage
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0072_report_ready_owner_envelope"
down_revision = "0071_data_access_lineage"
branch_labels = None
depends_on = None


_UUID_PATTERN = (
    "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_TRIGGER_NAME = "trg_report_ready_owner_envelope"
_FUNCTION_NAME = "threatlens_report_ready_owner_envelope"


def _valid_legacy_event(alias: str) -> str:
    return f"""
        COALESCE((
            {alias}.event_type = 'report_ready'
            AND {alias}.schema_version = 1
            AND {alias}.source_type = 'report'
            AND {alias}.source_id ~ '{_UUID_PATTERN}'
            AND jsonb_typeof({alias}.payload_json::jsonb) = 'object'
            AND {alias}.payload_json->>'report_id' = {alias}.source_id
            AND jsonb_typeof({alias}.payload_json::jsonb->'daily_brief') = 'object'
            AND {alias}.payload_json::jsonb->'daily_brief'->>'id' = {alias}.source_id
            AND {alias}.actor_user_id IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM reports AS report
                WHERE report.id = {alias}.source_id::uuid
                  AND report.owner_user_id = {alias}.actor_user_id
            )
            AND EXISTS (
                SELECT 1 FROM users AS owner
                WHERE owner.id = {alias}.actor_user_id
            )
        ), false)
    """


def _valid_v2_event(alias: str) -> str:
    return f"""
        COALESCE((
            {alias}.event_type = 'report_ready'
            AND {alias}.schema_version = 2
            AND {alias}.source_type = 'report'
            AND {alias}.source_id ~ '{_UUID_PATTERN}'
            AND jsonb_typeof({alias}.payload_json::jsonb) = 'object'
            AND {alias}.payload_json->>'report_id' = {alias}.source_id
            AND jsonb_typeof({alias}.payload_json::jsonb->'daily_brief') = 'object'
            AND {alias}.payload_json::jsonb->'daily_brief'->>'id' = {alias}.source_id
            AND {alias}.payload_json->>'owner_user_id' =
                {alias}.actor_user_id::text
            AND {alias}.actor_user_id IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM reports AS report
                WHERE report.id = {alias}.source_id::uuid
                  AND report.owner_user_id = {alias}.actor_user_id
            )
            AND EXISTS (
                SELECT 1 FROM users AS owner
                WHERE owner.id = {alias}.actor_user_id
            )
        ), false)
    """


def _safe_delivery(alias: str, event_alias: str, instance_alias: str) -> str:
    return f"""
        COALESCE((
            {_valid_v2_event(event_alias)}
            AND {alias}.event_type = 'report_ready'
            AND {alias}.connector_type = {instance_alias}.integration_type
            AND (
                {instance_alias}.owner_user_id = {event_alias}.actor_user_id
                OR (
                    {instance_alias}.integration_type = 'smtp'
                    AND {instance_alias}.owner_user_id IS NULL
                )
            )
        ), false)
    """


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            LOCK TABLE integration_events, integration_deliveries,
                integration_attempts, notification_webhook_deliveries,
                integration_instances, reports, users
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
    )
    valid_legacy = _valid_legacy_event("event")
    bind.execute(
        sa.text(
            f"""
            UPDATE integration_events AS event
            SET schema_version = 2,
                payload_json = jsonb_set(
                    jsonb_set(
                        event.payload_json::jsonb,
                        '{{owner_user_id}}',
                        to_jsonb(event.actor_user_id::text),
                        true
                    ),
                    '{{schema_version}}',
                    '2'::jsonb,
                    true
                )
            WHERE {valid_legacy}
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE integration_events AS event
            SET routing_state = 'dead_letter',
                claimed_at = NULL,
                routed_at = NULL,
                available_at = now(),
                last_error =
                    'event_envelope: legacy report_ready ownership could not be verified'
            WHERE event.event_type = 'report_ready'
              AND event.schema_version = 1
              AND NOT ({valid_legacy})
            """
        )
    )

    safe_delivery = _safe_delivery("delivery", "event", "instance")
    bind.execute(
        sa.text(
            f"""
            UPDATE integration_deliveries AS delivery
            SET owner_user_id = event.actor_user_id,
                payload_json = jsonb_set(
                    jsonb_set(
                        CASE
                            WHEN jsonb_typeof(delivery.payload_json::jsonb) = 'object'
                            THEN delivery.payload_json::jsonb
                            ELSE '{{}}'::jsonb
                        END,
                        '{{owner_user_id}}',
                        to_jsonb(event.actor_user_id::text),
                        true
                    ),
                    '{{schema_version}}',
                    '2'::jsonb,
                    true
                )
            FROM integration_events AS event,
                 integration_instances AS instance
            WHERE delivery.event_id = event.id
              AND delivery.integration_id = instance.id
              AND {safe_delivery}
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE integration_deliveries AS delivery
            SET state = 'dead_letter',
                claimed_at = NULL,
                not_before = NULL,
                completed_at = COALESCE(delivery.completed_at, now()),
                dead_lettered_at = COALESCE(delivery.dead_lettered_at, now()),
                last_error_code = 'report_owner_envelope_invalid',
                last_error_message =
                    'Legacy report-ready delivery ownership could not be verified.',
                last_error_retryable = false
            WHERE delivery.event_type = 'report_ready'
              AND delivery.state NOT IN ('succeeded', 'failed', 'dead_letter')
              AND NOT EXISTS (
                  SELECT 1
                  FROM integration_events AS event
                  JOIN integration_instances AS instance
                    ON instance.id = delivery.integration_id
                  WHERE event.id = delivery.event_id
                    AND {safe_delivery}
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE integration_attempts AS attempt
            SET status = 'failed',
                finished_at = COALESCE(attempt.finished_at, now()),
                error_code = 'report_owner_envelope_invalid',
                error_message =
                    'Legacy report-ready delivery ownership could not be verified.',
                retryable = false,
                response_json = jsonb_set(
                    COALESCE(attempt.response_json::jsonb, '{}'::jsonb),
                    '{delivery_outcome}',
                    '"not_attempted"'::jsonb,
                    true
                )
            FROM integration_deliveries AS delivery
            WHERE attempt.delivery_id = delivery.id
              AND delivery.last_error_code = 'report_owner_envelope_invalid'
              AND attempt.status = 'running'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE notification_webhook_deliveries AS webhook_delivery
            SET delivery_state = 'failed',
                success = false,
                claimed_at = NULL,
                not_before = NULL,
                error =
                    'policy_error:Legacy report-ready delivery ownership could not be verified.'
            FROM integration_deliveries AS delivery
            WHERE webhook_delivery.integration_delivery_id = delivery.id
              AND delivery.last_error_code = 'report_owner_envelope_invalid'
              AND webhook_delivery.delivery_state NOT IN ('succeeded', 'failed')
            """
        )
    )
    _install_rolling_upgrade_trigger(bind)


def _install_rolling_upgrade_trigger(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION {_FUNCTION_NAME}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                valid_envelope boolean;
            BEGIN
                IF NEW.event_type <> 'report_ready' OR NEW.schema_version <> 1 THEN
                    RETURN NEW;
                END IF;

                valid_envelope := COALESCE((
                    NEW.source_type = 'report'
                    AND NEW.source_id ~ '{_UUID_PATTERN}'
                    AND jsonb_typeof(NEW.payload_json::jsonb) = 'object'
                    AND NEW.payload_json->>'report_id' = NEW.source_id
                    AND jsonb_typeof(NEW.payload_json::jsonb->'daily_brief') = 'object'
                    AND NEW.payload_json::jsonb->'daily_brief'->>'id' = NEW.source_id
                    AND NEW.actor_user_id IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM reports AS report
                        WHERE report.id = NEW.source_id::uuid
                          AND report.owner_user_id = NEW.actor_user_id
                    )
                    AND EXISTS (
                        SELECT 1 FROM users AS owner
                        WHERE owner.id = NEW.actor_user_id
                    )
                ), false);

                IF valid_envelope THEN
                    NEW.schema_version := 2;
                    NEW.payload_json := jsonb_set(
                        jsonb_set(
                            NEW.payload_json::jsonb,
                            '{{owner_user_id}}',
                            to_jsonb(NEW.actor_user_id::text),
                            true
                        ),
                        '{{schema_version}}',
                        '2'::jsonb,
                        true
                    );
                ELSE
                    NEW.routing_state := 'dead_letter';
                    NEW.claimed_at := NULL;
                    NEW.routed_at := NULL;
                    NEW.available_at := now();
                    NEW.last_error :=
                        'event_envelope: legacy report_ready ownership could not be verified';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TRIGGER {_TRIGGER_NAME}
            BEFORE INSERT OR UPDATE ON integration_events
            FOR EACH ROW
            EXECUTE FUNCTION {_FUNCTION_NAME}()
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON integration_events"))
    op.execute(sa.text(f"DROP FUNCTION IF EXISTS {_FUNCTION_NAME}()"))
    # Retaining schema v2 is fail-safe: older workers defer these events instead
    # of routing an ownerless v1 envelope to every configured destination.

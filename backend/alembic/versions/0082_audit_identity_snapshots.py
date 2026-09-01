"""add durable audit identity snapshots

Revision ID: 0082_audit_identity_snapshots
Revises: 0081_data_policy_activation
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0082_audit_identity_snapshots"
down_revision = "0081_data_policy_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("actor_label_snapshot", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("resource_label_snapshot", sa.String(length=320), nullable=True),
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs
            SET actor_principal_type = 'user',
                actor_principal_id = COALESCE(actor_principal_id, actor_user_id)
            WHERE actor_user_id IS NOT NULL
              AND (actor_principal_type IS NULL OR actor_principal_id IS NULL)
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs AS audit
            SET actor_label_snapshot = left(app_user.email, 320)
            FROM users AS app_user
            WHERE audit.actor_label_snapshot IS NULL
              AND NOT COALESCE((
                    audit.action IN (
                        'auth.register',
                        'auth.login',
                        'auth.login.mfa_challenge'
                    )
                    AND jsonb_typeof(audit.metadata_json -> 'email') = 'string'
                    AND btrim(audit.metadata_json ->> 'email') <> ''
              ), false)
              AND (
                    audit.actor_user_id = app_user.id
                    OR (
                        audit.actor_principal_type = 'user'
                        AND audit.actor_principal_id = app_user.id
                    )
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs AS audit
            SET actor_label_snapshot = left(service_account.name, 320)
            FROM service_accounts AS service_account
            WHERE audit.actor_label_snapshot IS NULL
              AND audit.actor_principal_type = 'service_account'
              AND audit.actor_principal_id = service_account.id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs
            SET actor_label_snapshot = left(btrim(metadata_json ->> 'email'), 320)
            WHERE actor_label_snapshot IS NULL
              AND action IN ('auth.register', 'auth.login', 'auth.login.mfa_challenge')
              AND (actor_principal_type = 'user' OR actor_user_id IS NOT NULL)
              AND jsonb_typeof(metadata_json -> 'email') = 'string'
              AND btrim(metadata_json ->> 'email') <> ''
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs AS audit
            SET resource_label_snapshot = left(app_user.email, 320)
            FROM users AS app_user
            WHERE audit.resource_label_snapshot IS NULL
              AND audit.resource_type = 'user'
              AND NOT (
                    COALESCE((
                        jsonb_typeof(audit.metadata_json -> 'email_after_update') = 'string'
                        AND btrim(audit.metadata_json ->> 'email_after_update') <> ''
                    ), false)
                    OR COALESCE((
                        jsonb_typeof(audit.metadata_json -> 'email') = 'string'
                        AND btrim(audit.metadata_json ->> 'email') <> ''
                    ), false)
              )
              AND audit.resource_id = app_user.id::text
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs
            SET resource_label_snapshot = left(
                btrim(
                    CASE
                        WHEN jsonb_typeof(metadata_json -> 'email_after_update') = 'string'
                             AND btrim(metadata_json ->> 'email_after_update') <> ''
                            THEN metadata_json ->> 'email_after_update'
                        ELSE metadata_json ->> 'email'
                    END
                ),
                320
            )
            WHERE resource_label_snapshot IS NULL
              AND resource_type = 'user'
              AND (
                    (
                        jsonb_typeof(metadata_json -> 'email_after_update') = 'string'
                        AND btrim(metadata_json ->> 'email_after_update') <> ''
                    )
                    OR (
                        jsonb_typeof(metadata_json -> 'email') = 'string'
                        AND btrim(metadata_json ->> 'email') <> ''
                    )
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE audit_logs AS audit
            SET resource_label_snapshot = left(service_account.name, 320)
            FROM service_accounts AS service_account
            WHERE audit.resource_label_snapshot IS NULL
              AND audit.resource_type = 'service_account'
              AND audit.resource_id = service_account.id::text
            """
        )
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "resource_label_snapshot")
    op.drop_column("audit_logs", "actor_label_snapshot")

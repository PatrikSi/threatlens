"""guard unfenced report workers during rolling upgrades

Revision ID: 0052_legacy_worker_guard
Revises: 0051_report_dispatch_claims
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op


revision = "0052_legacy_worker_guard"
down_revision = "0051_report_dispatch_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Workers released before report fencing existed can be generating a report
    # without either lease column. Protect that work for one day so a new worker
    # cannot repeat provider calls during a rolling deployment.
    op.execute(
        """
        UPDATE reports AS report
        SET generation_lease_token =
                'legacy-unfenced:' || replace(report.id::text, '-', ''),
            generation_lease_expires_at = now() + interval '24 hours'
        WHERE report.status = 'running'
          AND report.generation_lease_token IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM report_generation_leases AS lease
              WHERE lease.report_id = report.id
                AND lease.lease_token IS NOT NULL
          )
        """
    )
    op.execute(
        """
        INSERT INTO report_generation_leases (
            report_id,
            generation_fence,
            lease_token,
            lease_expires_at
        )
        SELECT
            id,
            1,
            generation_lease_token,
            generation_lease_expires_at
        FROM reports
        WHERE status = 'running'
          AND generation_lease_token LIKE 'legacy-unfenced:%'
        ON CONFLICT (report_id) DO UPDATE
        SET generation_fence = report_generation_leases.generation_fence + 1,
            lease_token = EXCLUDED.lease_token,
            lease_expires_at = EXCLUDED.lease_expires_at
        WHERE report_generation_leases.lease_token IS NULL
        """
    )


def downgrade() -> None:
    # Keep compatibility leases when rolling back to 0051. Clearing them while
    # an old worker is active would reintroduce duplicate generation risk.
    pass

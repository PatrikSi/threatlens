"""Hide expired permission history before bounded provenance cleanup.

Revision ID: 0094_permission_history_pruning
Revises: 0093_incremental_history_pruning
"""

import sqlalchemy as sa
from alembic import op


revision = "0094_permission_history_pruning"
down_revision = "0093_incremental_history_pruning"
branch_labels = None
depends_on = None

_PARENTS = ("audit_logs", "alert_occurrence_metrics", "integration_delivery_metrics")
_METRICS = ("alert_occurrence", "integration_delivery")


def upgrade() -> None:
    for table in _PARENTS:
        op.add_column(
            table, sa.Column("retention_pruning_started_at", sa.DateTime(timezone=True))
        )
        ignored = "actor_user_id" if table == "audit_logs" else ""
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_permission_history_parent_update BEFORE UPDATE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION threatlens_guard_pruning_parent('{ignored}')"
            )
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_permission_history_parent_delete AFTER DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION threatlens_guard_pruning_parent('')"
            )
        )
    for table in ("audit_log_data_access_labels", "audit_log_data_access_feeds"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_permission_history_reference BEFORE INSERT OR UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION threatlens_guard_pruning_reference('audit_logs', 'audit_log_id', 'ignore', 'always')"
            )
        )
    _install_audit_mutation_fence()
    _install_metric_reference_fences()
    for prefix in _METRICS:
        _metric_mutation_fence(prefix, pruning=True)


def downgrade() -> None:
    # Partially deleted labels cannot be made readable by an old binary. Finish
    # their bounded cleanup before a downgrade, rather than dropping the fence.
    for table in _PARENTS:
        if op.get_bind().scalar(
            sa.text(
                f"SELECT EXISTS (SELECT 1 FROM {table} WHERE retention_pruning_started_at IS NOT NULL)"
            )
        ):
            raise RuntimeError(
                "Finish permission-history retention cleanup before downgrading."
            )
    for prefix in _METRICS:
        _metric_mutation_fence(prefix, pruning=False)
        cohort_table = f"{prefix}_metric_cohorts"
        op.execute(
            sa.text(f"DROP TRIGGER trg_permission_cohort_reference ON {cohort_table}")
        )
        for table in _cohort_children(prefix):
            op.execute(
                sa.text(
                    f"DROP TRIGGER trg_permission_cohort_child_reference ON {table}"
                )
            )
    op.execute(sa.text("DROP FUNCTION threatlens_guard_permission_cohort_child()"))
    op.execute(sa.text("DROP FUNCTION threatlens_guard_permission_cohort()"))
    for table in ("audit_log_data_access_labels", "audit_log_data_access_feeds"):
        op.execute(sa.text(f"DROP TRIGGER trg_permission_history_reference ON {table}"))
        op.execute(
            sa.text(f"DROP TRIGGER trg_permission_audit_immutability ON {table}")
        )
    op.execute(sa.text("DROP FUNCTION threatlens_guard_audit_history_provenance()"))
    for table in reversed(_PARENTS):
        op.execute(
            sa.text(f"DROP TRIGGER trg_permission_history_parent_update ON {table}")
        )
        op.execute(
            sa.text(f"DROP TRIGGER trg_permission_history_parent_delete ON {table}")
        )
        op.drop_column(table, "retention_pruning_started_at")


def _cohort_children(prefix: str) -> tuple[str, ...]:
    suffixes = ("labels", "captured_labels", "taint_labels")
    if prefix == "integration_delivery":
        suffixes += ("feeds",)
    return tuple(f"{prefix}_metric_cohort_{suffix}" for suffix in suffixes)


def _install_audit_mutation_fence() -> None:
    op.execute(
        sa.text("""
        CREATE FUNCTION threatlens_guard_audit_history_provenance()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND EXISTS (
                SELECT 1 FROM audit_logs AS audit
                JOIN lifecycle_pruning_records AS pruning
                  ON pruning.dataset = 'audit_logs' AND pruning.parent_id = audit.id
                WHERE audit.id = OLD.audit_log_id
                  AND audit.retention_pruning_started_at IS NOT NULL
            ) THEN
                RETURN OLD;
            END IF;
            IF TG_OP = 'UPDATE' OR EXISTS (
                SELECT 1 FROM audit_logs WHERE id = OLD.audit_log_id
            ) THEN
                RAISE EXCEPTION 'Retained audit provenance is immutable.' USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
        END;
        $$
    """)
    )
    for table in ("audit_log_data_access_labels", "audit_log_data_access_feeds"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_permission_audit_immutability BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION threatlens_guard_audit_history_provenance()"
            )
        )


def _install_metric_reference_fences() -> None:
    op.execute(
        sa.text("""
        CREATE FUNCTION threatlens_guard_permission_cohort()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                PERFORM threatlens_assert_history_reference(TG_ARGV[0], OLD.metric_id);
            END IF;
            PERFORM threatlens_assert_history_reference(TG_ARGV[0], NEW.metric_id);
            RETURN NEW;
        END;
        $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION threatlens_guard_permission_cohort_child()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_key uuid;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                EXECUTE format('SELECT metric_id FROM %I WHERE id = $1', TG_ARGV[1])
                    INTO parent_key USING OLD.cohort_id;
                IF parent_key IS NOT NULL THEN
                    PERFORM threatlens_assert_history_reference(TG_ARGV[0], parent_key);
                END IF;
            END IF;
            EXECUTE format('SELECT metric_id FROM %I WHERE id = $1', TG_ARGV[1])
                INTO parent_key USING NEW.cohort_id;
            -- A feed relabel may have selected a cohort immediately before its
            -- expired parent committed a claim or its empty cohort was removed.
            IF parent_key IS NULL OR NOT threatlens_assert_history_reference(
                TG_ARGV[0], parent_key, true
            ) THEN
                RETURN NULL;
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    )
    for prefix in _METRICS:
        parent = f"{prefix}_metrics"
        cohort = f"{prefix}_metric_cohorts"
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_permission_cohort_reference BEFORE INSERT OR UPDATE ON {cohort} "
                f"FOR EACH ROW EXECUTE FUNCTION threatlens_guard_permission_cohort('{parent}')"
            )
        )
        for table in _cohort_children(prefix):
            op.execute(
                sa.text(
                    f"CREATE TRIGGER trg_permission_cohort_child_reference BEFORE INSERT OR UPDATE ON {table} "
                    f"FOR EACH ROW EXECUTE FUNCTION threatlens_guard_permission_cohort_child('{parent}', '{cohort}')"
                )
            )


def _metric_mutation_fence(prefix: str, *, pruning: bool) -> None:
    short = "alert" if prefix == "alert_occurrence" else "integration"
    exception = ""
    if pruning:
        exception = f"""
            IF TG_OP = 'DELETE' AND EXISTS (
                SELECT 1 FROM {prefix}_metric_cohorts AS cohort
                JOIN {prefix}_metrics AS metric ON metric.id = cohort.metric_id
                JOIN lifecycle_pruning_records AS pruning
                  ON pruning.dataset = '{prefix}_metrics'
                 AND pruning.parent_id = metric.id
                WHERE cohort.id = OLD.cohort_id
                  AND metric.retention_pruning_started_at IS NOT NULL
            ) THEN
                RETURN OLD;
            END IF;
        """
    op.execute(
        sa.text(f"""
        CREATE OR REPLACE FUNCTION threatlens_guard_{short}_metric_labels_v1()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            {exception}
            IF TG_OP = 'UPDATE' OR EXISTS (
                SELECT 1 FROM {prefix}_metric_cohorts WHERE id = OLD.cohort_id
            ) THEN
                RAISE EXCEPTION
                    'Metric cohort label provenance is immutable while its cohort is retained.'
                    USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
        END;
        $$
    """)
    )

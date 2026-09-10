"""Persist bounded history pruning and fence new references while it progresses."""
from alembic import op
import sqlalchemy as sa

revision = "0093_incremental_history_pruning"
down_revision = "0092_operations_freshness"
branch_labels = None
depends_on = None

PARENTS = {
    "ai_task_runs": "actor_user_id,item_id,daily_brief_id,report_id,parent_run_id,superseded_by_task_run_id",
    "integration_deliveries": "subscription_id,event_id,requested_by_user_id,source_delivery_id",
    "alert_evaluation_requests": "",
    "action_approval_requests": "requested_by_user_id,decided_by_user_id,executed_by_user_id",
}
# Name, child table, parent table, child reference column, update behavior.
REFERENCES = (
    ("ai_events", "ai_task_events", "ai_task_runs", "task_run_id", "changed"),
    ("ai_children", "ai_task_runs", "ai_task_runs", "parent_run_id", "changed"),
    ("ai_superseding", "ai_task_runs", "ai_task_runs", "superseded_by_task_run_id", "changed"),
    ("ai_reports", "reports", "ai_task_runs", "initial_task_run_id", "changed"),
    ("ai_receipts", "ai_provider_attempt_receipts", "ai_task_runs", "task_run_id_snapshot", "always"),
    ("integration_attempts", "integration_attempts", "integration_deliveries", "delivery_id", "changed"),
    ("integration_retries", "integration_deliveries", "integration_deliveries", "source_delivery_id", "changed"),
    ("integration_webhooks", "notification_webhook_deliveries", "integration_deliveries", "integration_delivery_id", "changed"),
    ("evaluation_activities", "alert_evaluation_request_activities", "alert_evaluation_requests", "request_id", "changed"),
    ("evaluation_matches", "alert_evaluation_matches", "alert_evaluation_requests", "request_id", "changed"),
    ("approval_receipts", "action_execution_receipts", "action_approval_requests", "approval_request_id", "changed"),
)


def upgrade() -> None:
    op.create_table(
        "lifecycle_pruning_records",
        sa.Column("dataset", sa.String(64), primary_key=True),
        sa.Column("parent_id", sa.Uuid(), primary_key=True),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("children_pruned", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("children_pruned >= 0", name="ck_lifecycle_pruning_nonnegative"),
    )
    op.execute("""
        CREATE FUNCTION threatlens_assert_history_reference(parent_table text, parent_key uuid, ignore_write boolean DEFAULT false)
        RETURNS boolean LANGUAGE plpgsql AS $$
        BEGIN
          IF parent_key IS NULL THEN RETURN true; END IF;
          -- Fast rejection avoids taking a source lock while an UPDATE already
          -- owns a child row. Recheck after the source lock closes claim races.
          IF EXISTS (SELECT 1 FROM lifecycle_pruning_records WHERE dataset = parent_table AND parent_id = parent_key) THEN
            IF ignore_write THEN RETURN false; END IF;
            RAISE EXCEPTION 'Expired history cleanup is in progress; its references cannot be reused.' USING ERRCODE = '55000';
          END IF;
          EXECUTE format('SELECT id FROM %I WHERE id = $1 FOR KEY SHARE', parent_table) USING parent_key;
          IF EXISTS (SELECT 1 FROM lifecycle_pruning_records WHERE dataset = parent_table AND parent_id = parent_key) THEN
            IF ignore_write THEN RETURN false; END IF;
            RAISE EXCEPTION 'Expired history cleanup is in progress; its references cannot be reused.' USING ERRCODE = '55000';
          END IF;
          RETURN true;
        END;
        $$;
        CREATE FUNCTION threatlens_guard_pruning_reference()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_key uuid;
        BEGIN
          IF TG_OP = 'UPDATE' AND COALESCE(TG_ARGV[3], 'changed') <> 'always'
             AND (to_jsonb(NEW)->TG_ARGV[1]) IS NOT DISTINCT FROM (to_jsonb(OLD)->TG_ARGV[1]) THEN
            RETURN NEW;
          END IF;
          parent_key := (to_jsonb(NEW)->>TG_ARGV[1])::uuid;
          IF NOT threatlens_assert_history_reference(TG_ARGV[0], parent_key, COALESCE(TG_ARGV[2], '') = 'ignore') THEN
            RETURN NULL;
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE FUNCTION threatlens_guard_pruning_parent()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          ignored_columns text[] := CASE WHEN COALESCE(TG_ARGV[0], '') = '' THEN ARRAY[]::text[] ELSE string_to_array(TG_ARGV[0], ',') END;
          field text;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            DELETE FROM lifecycle_pruning_records WHERE dataset = TG_TABLE_NAME AND parent_id = OLD.id;
            RETURN OLD;
          END IF;
          IF EXISTS (SELECT 1 FROM lifecycle_pruning_records WHERE dataset = TG_TABLE_NAME AND parent_id = OLD.id) THEN
            IF (to_jsonb(NEW) - ignored_columns) IS DISTINCT FROM (to_jsonb(OLD) - ignored_columns) THEN
              RAISE EXCEPTION 'Expired history cleanup is in progress; its parent cannot be changed.' USING ERRCODE = '55000';
            END IF;
            FOREACH field IN ARRAY ignored_columns LOOP
              IF (to_jsonb(NEW)->field) IS DISTINCT FROM (to_jsonb(OLD)->field) AND to_jsonb(NEW)->>field IS NOT NULL THEN
                RAISE EXCEPTION 'Expired history cleanup permits only reference detachment.' USING ERRCODE = '55000';
              END IF;
            END LOOP;
          END IF;
          RETURN NEW;
        END;
        $$;
    """)
    for table, ignored_columns in PARENTS.items():
        op.execute(f"CREATE TRIGGER trg_pruning_parent_update BEFORE UPDATE ON {table} FOR EACH ROW "
                   f"EXECUTE FUNCTION threatlens_guard_pruning_parent('{ignored_columns}')")
        op.execute(f"CREATE TRIGGER trg_pruning_parent_deleted AFTER DELETE ON {table} FOR EACH ROW "
                   "EXECUTE FUNCTION threatlens_guard_pruning_parent()")
    for name, table, parent, column, behavior in REFERENCES:
        op.execute(f"CREATE TRIGGER trg_pruning_{name} BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW "
                   f"EXECUTE FUNCTION threatlens_guard_pruning_reference('{parent}', '{column}', '', '{behavior}')")
    _install_semantic_reference_guards()


def _install_semantic_reference_guards() -> None:
    op.execute("""
        CREATE FUNCTION threatlens_guard_pruning_semantic_reference()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source_id uuid;
        BEGIN
          IF TG_TABLE_NAME = 'governance_operation_receipts' THEN
            IF NEW.resource_type = 'action_approval' THEN
              PERFORM threatlens_assert_history_reference('action_approval_requests', NEW.resource_id);
            END IF;
          ELSE
            IF NEW.data_access_source_type = 'ai_task_run' THEN
              PERFORM threatlens_assert_history_reference('ai_task_runs', NEW.data_access_source_id);
            END IF;
            IF NEW.target_type = 'ai_provider_attempt_receipt'
               AND NEW.target_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
              SELECT task_run_id_snapshot INTO source_id FROM ai_provider_attempt_receipts WHERE id = NEW.target_id::uuid;
              PERFORM threatlens_assert_history_reference('ai_task_runs', source_id);
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_pruning_approval_source BEFORE INSERT OR UPDATE ON action_approval_requests
          FOR EACH ROW EXECUTE FUNCTION threatlens_guard_pruning_semantic_reference();
        CREATE TRIGGER trg_pruning_governance_receipt BEFORE INSERT OR UPDATE ON governance_operation_receipts
          FOR EACH ROW EXECUTE FUNCTION threatlens_guard_pruning_semantic_reference();
    """)


def downgrade() -> None:
    for trigger, table in (("trg_pruning_approval_source", "action_approval_requests"),
                           ("trg_pruning_governance_receipt", "governance_operation_receipts")):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    op.execute("DROP FUNCTION threatlens_guard_pruning_semantic_reference()")
    for name, table, *_ in REFERENCES:
        op.execute(f"DROP TRIGGER trg_pruning_{name} ON {table}")
    for table in PARENTS:
        op.execute(f"DROP TRIGGER trg_pruning_parent_update ON {table}")
        op.execute(f"DROP TRIGGER trg_pruning_parent_deleted ON {table}")
    op.execute("DROP FUNCTION threatlens_guard_pruning_parent()")
    op.execute("DROP FUNCTION threatlens_guard_pruning_reference()")
    op.execute("DROP FUNCTION threatlens_assert_history_reference(text, uuid, boolean)")
    op.drop_table("lifecycle_pruning_records")

"""Add team-owned watchlists, assignment and bounded escalation state."""

from alembic import op
import sqlalchemy as sa

revision = "0103_shared_triage_queues"
down_revision = "0102_named_team_workspaces"
branch_labels = None
depends_on = None

OWNERS = {
    "alert_interests": "user_id",
    "alert_evaluation_matches": "owner_user_id",
    "alert_occurrences": "owner_user_id",
    "alert_occurrence_metrics": "owner_user_id",
}
METRIC_DIMENSIONS = [
    "bucket_start",
    "owner_user_id",
    "severity",
    "lifecycle_state",
    "suppressed",
]


def upgrade() -> None:
    for table, owner in OWNERS.items():
        op.add_column(
            table,
            sa.Column(
                "team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="RESTRICT")
            ),
        )
        op.create_index(f"ix_{table}_team_id", table, ["team_id"])
        op.alter_column(table, owner, nullable=True)
        op.create_check_constraint(
            f"ck_{table}_owner",
            table,
            f"({owner} IS NOT NULL AND team_id IS NULL) OR ({owner} IS NULL AND team_id IS NOT NULL)",
        )
    for table in ("alert_interests", "alert_evaluation_matches"):
        op.add_column(table, sa.Column("due_after_minutes", sa.Integer()))
        op.add_column(table, sa.Column("escalation_after_minutes", sa.Integer()))
        op.create_check_constraint(
            f"ck_{table}_due_minutes", table, "due_after_minutes BETWEEN 1 AND 525600"
        )
        op.create_check_constraint(
            f"ck_{table}_escalation_minutes",
            table,
            "escalation_after_minutes BETWEEN 0 AND 525600",
        )
        op.create_check_constraint(
            f"ck_{table}_escalation_due",
            table,
            "escalation_after_minutes IS NULL OR due_after_minutes IS NOT NULL",
        )
    op.add_column(
        "alert_occurrences",
        sa.Column(
            "assignee_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("alert_occurrences", sa.Column("due_at", sa.DateTime(timezone=True)))
    op.add_column(
        "alert_occurrences", sa.Column("escalation_after_minutes", sa.Integer())
    )
    op.add_column(
        "alert_occurrences", sa.Column("escalated_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "ix_alert_occurrences_assignee_user_id",
        "alert_occurrences",
        ["assignee_user_id"],
    )
    op.create_index(
        "ix_alert_occurrences_team_state_due",
        "alert_occurrences",
        ["team_id", "lifecycle_state", "due_at"],
    )
    op.drop_constraint(
        "uq_alert_occurrence_metrics_bucket_dimensions",
        "alert_occurrence_metrics",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_alert_occurrence_metrics_bucket_dimensions",
        "alert_occurrence_metrics",
        [*METRIC_DIMENSIONS[:2], "team_id", *METRIC_DIMENSIONS[2:]],
        postgresql_nulls_not_distinct=True,
    )
    _update_rule_trigger(extended=True)
    op.execute("""
        CREATE FUNCTION threatlens_alert_team_ownership_immutable()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.team_id IS DISTINCT FROM OLD.team_id THEN
                RAISE EXCEPTION 'Alert team ownership cannot change after creation'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_alert_team_ownership_immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    for table in OWNERS:
        op.execute(
            f"CREATE TRIGGER trg_alert_team_ownership_immutable BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION threatlens_alert_team_ownership_immutable()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if any(
        connection.scalar(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE team_id IS NOT NULL)")
        )
        for table in OWNERS
    ):
        raise RuntimeError(
            "Back up and remove team alert rules, queued matches, occurrences and metrics before downgrading shared triage."
        )
    for table in OWNERS:
        op.execute(f"DROP TRIGGER trg_alert_team_ownership_immutable ON {table}")
    op.execute("DROP FUNCTION threatlens_alert_team_ownership_immutable()")
    _update_rule_trigger(extended=False)
    op.drop_constraint(
        "uq_alert_occurrence_metrics_bucket_dimensions",
        "alert_occurrence_metrics",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_alert_occurrence_metrics_bucket_dimensions",
        "alert_occurrence_metrics",
        METRIC_DIMENSIONS,
    )
    op.drop_index("ix_alert_occurrences_team_state_due", table_name="alert_occurrences")
    op.drop_index(
        "ix_alert_occurrences_assignee_user_id", table_name="alert_occurrences"
    )
    for field in (
        "assignee_user_id",
        "due_at",
        "escalation_after_minutes",
        "escalated_at",
    ):
        op.drop_column("alert_occurrences", field)
    for table in ("alert_interests", "alert_evaluation_matches"):
        for suffix in ("due_minutes", "escalation_minutes", "escalation_due"):
            op.drop_constraint(f"ck_{table}_{suffix}", table, type_="check")
        op.drop_column(table, "due_after_minutes")
        op.drop_column(table, "escalation_after_minutes")
    for table, owner in OWNERS.items():
        op.drop_constraint(f"ck_{table}_owner", table, type_="check")
        op.alter_column(table, owner, nullable=False)
        op.drop_index(f"ix_{table}_team_id", table_name=table)
        op.drop_column(table, "team_id")


def _update_rule_trigger(*, extended: bool) -> None:
    """Retain the installed compatibility contract and extend semantic changes.

    A guarded replacement avoids copying the full historical trigger into another
    migration. Unexpected customized definitions fail visibly before any rollout.
    """
    baseline = "OR NEW.severity IS DISTINCT FROM OLD.severity;"
    with_deadlines = """OR NEW.severity IS DISTINCT FROM OLD.severity
                    OR NEW.due_after_minutes IS DISTINCT FROM OLD.due_after_minutes
                    OR NEW.escalation_after_minutes IS DISTINCT FROM OLD.escalation_after_minutes;"""
    definition = op.get_bind().scalar(
        sa.text(
            "SELECT pg_get_functiondef('threatlens_alert_interests_v2_compat'::regproc)"
        )
    )
    before, after = (
        (baseline, with_deadlines) if extended else (with_deadlines, baseline)
    )
    if not isinstance(definition, str) or definition.count(before) != 1:
        raise RuntimeError(
            "The alert compatibility trigger has an unexpected definition; inspect it before migrating team deadlines."
        )
    op.execute(sa.text(definition.replace(before, after)))

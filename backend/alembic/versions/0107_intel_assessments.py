"""Preserve indicator evidence and team-scoped review history."""

from alembic import op
import sqlalchemy as sa

revision = "0107_intel_assessments"
down_revision = "0106_article_team_intelligence"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _time(name="updated_at"):
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _actor(name="updated_by_user_id"):
    return sa.Column(name, sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))


def upgrade():
    # PostgreSQL btree entries cannot hold every supported 4 KiB URL. A stored
    # UTF-8 SHA-256 key preserves full values and works for older worker inserts.
    op.execute(
        sa.text("""
        CREATE FUNCTION threatlens_indicator_digest(value text) RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path = pg_catalog AS $$
          SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(value, 'UTF8')), 'hex')
        $$
    """)
    )
    op.add_column(
        "iocs",
        sa.Column(
            "value_digest",
            sa.String(64),
            sa.Computed("threatlens_indicator_digest(value_norm)", persisted=True),
            nullable=False,
        ),
    )
    op.drop_constraint("uq_iocs_type_value_norm", "iocs", type_="unique")
    op.create_unique_constraint(
        "uq_iocs_type_value_norm", "iocs", ["type", "value_digest"]
    )
    op.drop_index("ix_iocs_value_norm", table_name="iocs")
    op.create_index(
        "ix_iocs_value_norm", "iocs", ["value_norm"], postgresql_using="hash"
    )
    op.add_column(
        "item_iocs",
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "item_intel_states",
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source_revision", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "handling_label_id",
            sa.Uuid(),
            sa.ForeignKey("handling_labels.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "source_fingerprint", sa.String(64), nullable=False, server_default=""
        ),
        sa.Column(
            "indicator_set_hash", sa.String(64), nullable=False, server_default=""
        ),
        sa.Column(
            "extraction_fingerprint", sa.String(64), nullable=False, server_default=""
        ),
        _time(),
    )
    op.create_table(
        "team_intel_states",
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "indicator_set_hash", sa.String(64), nullable=False, server_default=""
        ),
    )
    op.create_table(
        "indicator_assessments",
        _id(),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ioc_id",
            sa.Uuid(),
            sa.ForeignKey("iocs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "handling_label_id",
            sa.Uuid(),
            sa.ForeignKey("handling_labels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.BigInteger(), nullable=False),
        sa.Column("extraction_revision", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        _actor(),
        _time(),
        sa.UniqueConstraint(
            "team_id", "item_id", "ioc_id", name="uq_indicator_assessments_scope"
        ),
    )
    for name in ("team_id", "item_id"):
        op.create_index(
            f"ix_indicator_assessments_{name}", "indicator_assessments", [name]
        )
    op.create_table(
        "indicator_suppressions",
        _id(),
        sa.Column(
            "team_id",
            sa.Uuid(),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ioc_type", sa.String(32), nullable=False),
        sa.Column("value_norm", sa.Text(), nullable=False),
        sa.Column(
            "value_digest",
            sa.String(64),
            sa.Computed("threatlens_indicator_digest(value_norm)", persisted=True),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        _actor(),
        _time(),
        sa.UniqueConstraint(
            "team_id",
            "ioc_type",
            "value_digest",
            name="uq_indicator_suppressions_scope",
        ),
    )
    op.create_index(
        "ix_indicator_suppressions_team_id", "indicator_suppressions", ["team_id"]
    )
    for table, parent, fk, constraint in (
        (
            "indicator_assessment_history",
            "indicator_assessments",
            "assessment_id",
            "uq_indicator_assessment_history_version",
        ),
        (
            "indicator_suppression_history",
            "indicator_suppressions",
            "suppression_id",
            "uq_indicator_suppression_history_version",
        ),
    ):
        op.create_table(
            table,
            _id(),
            sa.Column(
                fk,
                sa.Uuid(),
                sa.ForeignKey(f"{parent}.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("snapshot_json", sa.JSON(), nullable=False),
            _actor("actor_user_id"),
            _time("created_at"),
            sa.UniqueConstraint(fk, "version", name=constraint),
        )
        op.create_index(f"ix_{table}_{fk}", table, [fk])


def downgrade():
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM iocs WHERE octet_length(value_norm) > 2000)"
        )
    ).scalar():
        raise RuntimeError(
            "Cannot restore the previous IOC indexes while long indicator values remain. Export and remove those values before downgrading."
        )
    for table in (
        "team_intel_states",
        "indicator_suppression_history",
        "indicator_assessment_history",
        "indicator_suppressions",
        "indicator_assessments",
        "item_intel_states",
    ):
        op.drop_table(table)
    op.drop_column("item_iocs", "evidence_json")

    op.drop_index("ix_iocs_value_norm", table_name="iocs")
    op.create_index("ix_iocs_value_norm", "iocs", ["value_norm"])
    op.drop_constraint("uq_iocs_type_value_norm", "iocs", type_="unique")
    op.create_unique_constraint(
        "uq_iocs_type_value_norm", "iocs", ["type", "value_norm"]
    )
    op.drop_column("iocs", "value_digest")
    op.execute(sa.text("DROP FUNCTION threatlens_indicator_digest(text)"))

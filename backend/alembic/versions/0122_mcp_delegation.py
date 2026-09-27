"""Audience-bound MCP OAuth delegation with one-time PKCE authorization codes."""

from alembic import op
import sqlalchemy as sa

revision = "0122_mcp_delegation"
down_revision = "0121_publication_distribution"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "mcp_oauth_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column(
            "client_id",
            sa.Uuid(),
            sa.ForeignKey("mcp_oauth_clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("auth_token_version", sa.Integer(), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("resource", sa.String(2048), nullable=False),
        sa.Column("challenge", sa.String(43), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("label_cap_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_mcp_oauth_codes_expires_at", "mcp_oauth_codes", ["expires_at"])
    op.create_table(
        "mcp_delegations",
        sa.Column(
            "token_id",
            sa.Uuid(),
            sa.ForeignKey("api_tokens.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "client_id",
            sa.Uuid(),
            sa.ForeignKey("mcp_oauth_clients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("resource", sa.String(2048), nullable=False),
        sa.Column("label_cap_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_mcp_delegations_client_id", "mcp_delegations", ["client_id"])


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM mcp_delegations d JOIN api_tokens t ON t.id=d.token_id WHERE t.revoked_at IS NULL AND t.expires_at > now())"
        )
    ):
        raise RuntimeError("Revoke active MCP delegated credentials before downgrade")
    op.drop_table("mcp_delegations")
    op.drop_table("mcp_oauth_codes")
    op.drop_table("mcp_oauth_clients")

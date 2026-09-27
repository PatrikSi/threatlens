"""Pre-registered public OAuth clients and narrowly audience-bound MCP grants."""

from datetime import datetime
import uuid
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class MCPOAuthClient(Base):
    __tablename__ = "mcp_oauth_clients"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MCPOAuthCode(Base):
    __tablename__ = "mcp_oauth_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mcp_oauth_clients.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    auth_token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    resource: Mapped[str] = mapped_column(String(2048), nullable=False)
    challenge: Mapped[str] = mapped_column(String(43), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    label_cap_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MCPDelegation(Base):
    __tablename__ = "mcp_delegations"
    token_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("api_tokens.id", ondelete="CASCADE"), primary_key=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("mcp_oauth_clients.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    resource: Mapped[str] = mapped_column(String(2048), nullable=False)
    label_cap_json: Mapped[dict] = mapped_column(JSON, nullable=False)

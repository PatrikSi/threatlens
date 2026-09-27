"""A credential-bound authorization for additional article evidence processing."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, JSON, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class AIArticleContinuation(Base):
    __tablename__ = "ai_article_continuations"
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    authorization_encrypted: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected_progress_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    section_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

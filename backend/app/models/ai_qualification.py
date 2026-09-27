"""Synthetic provider qualification, separate from human semantic approval."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class AIQualification(Base):
    __tablename__ = "ai_qualifications"
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ai_task_runs.id", ondelete="CASCADE"), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    provider_version: Mapped[int] = mapped_column(Integer, nullable=False)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    authorization_encrypted: Mapped[dict] = mapped_column(JSON, nullable=False)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features_json: Mapped[list] = mapped_column(JSON, nullable=False)
    results_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

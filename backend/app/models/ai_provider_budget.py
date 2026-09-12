"""Short admission locks and conservative provider workload reservations."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIProviderBudgetState(Base):
    __tablename__ = "ai_provider_budget_states"

    # No provider FK: admission transactions never join the provider/IAM lock graph.
    provider_key: Mapped[str] = mapped_column(String(64), primary_key=True)


class AIProviderBudgetReservation(Base):
    __tablename__ = "ai_provider_budget_reservations"
    __table_args__ = (
        Index("ix_ai_budget_created", "created_at"),
        Index("ix_ai_budget_provider_created", "provider_key", "created_at"),
        Index("ix_ai_budget_provider_active", "provider_key", "completed_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    charged_tokens: Mapped[int | None] = mapped_column(BigInteger)
    outcome: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

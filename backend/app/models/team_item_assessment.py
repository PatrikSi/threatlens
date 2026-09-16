"""Team-owned article assessments and immutable reviewed result revisions."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_JSON = JSON().with_variant(JSONB(), "postgresql")


class TeamItemAssessment(Base):
    __tablename__ = "team_item_assessments"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "item_id", name="uq_team_item_assessments_team_item"
        ),
        CheckConstraint("version >= 1", name="ck_team_item_assessments_version"),
        CheckConstraint(
            "principal_type = 'user'", name="ck_team_item_assessments_principal"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    task_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_task_runs.id", ondelete="SET NULL"),
        index=True,
    )
    context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    article_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    article_retrieved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    result_context_version: Mapped[int | None] = mapped_column(Integer)
    result_source_version: Mapped[int | None] = mapped_column(BigInteger)
    result_article_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    result_article_retrieved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    result_json: Mapped[dict | None] = mapped_column(_JSON)
    result_source_encrypted: Mapped[dict | None] = mapped_column(_JSON)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    principal_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user", server_default="user"
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    authorization_encrypted: Mapped[dict] = mapped_column(_JSON, nullable=False)
    source_encrypted: Mapped[dict | None] = mapped_column(_JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TeamAssessmentRevision(Base):
    __tablename__ = "team_assessment_revisions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_team_assessment_revisions_version"),
    )

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_item_assessments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    result_json: Mapped[dict] = mapped_column(_JSON, nullable=False)
    result_source_encrypted: Mapped[dict | None] = mapped_column(_JSON)
    result_context_version: Mapped[int] = mapped_column(Integer, nullable=False)
    result_source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    result_article_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    result_article_retrieved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

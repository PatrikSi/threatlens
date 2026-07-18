import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaggingSettings(Base):
    __tablename__ = "tagging_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    singleton_key: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1", unique=True)
    enabled_categories_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    min_auto_tag_confidence: Mapped[float] = mapped_column(nullable=False, default=0.45, server_default="0.45")
    secondary_tag_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

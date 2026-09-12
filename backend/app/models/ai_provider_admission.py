"""Operator limits shared by legacy and named provider admission controls."""

from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import Mapped, mapped_column


class AIProviderAdmissionLimits:
    max_concurrent_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    hourly_token_budget: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

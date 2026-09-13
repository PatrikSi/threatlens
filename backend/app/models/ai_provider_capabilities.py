"""Shared additive provider capabilities; defaults preserve legacy requests."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class AIProviderCapabilities:
    request_dialect: Mapped[str] = mapped_column(
        String(32), nullable=False, default="chat_completions", server_default="chat_completions"
    )
    reasoning_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    structured_output_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="off", server_default="off"
    )
    model_context_window_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

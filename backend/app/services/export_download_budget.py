"""One preparation deadline across authorization, materialization, and audit."""

from __future__ import annotations

from time import monotonic
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.budgets import DatabaseDeadlineExceeded, database_operation


@dataclass(frozen=True)
class ExportDownloadPreparationBudget:
    deadline: float

    @classmethod
    def start(cls) -> ExportDownloadPreparationBudget:
        return cls(monotonic() + get_settings().export_download_preparation_timeout_seconds)

    def remaining(self) -> float:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise DatabaseDeadlineExceeded("Export download preparation deadline exceeded")
        return remaining

    def checkpoint(self) -> None:
        self.remaining()

    @contextmanager
    def transaction(self, db: Session) -> Iterator[None]:
        # Each transaction receives the same absolute deadline. The helper
        # restores normal SQL limits on successful exit, preserving the locks
        # required for the independently bounded response transfer.
        with database_operation(db, operation="interactive", timeout_seconds=self.remaining()):
            yield
            self.checkpoint()

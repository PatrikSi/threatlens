"""Bounded pages of the exact source passage retained with a report."""

import uuid

from pydantic import BaseModel, ConfigDict


class ReportSourceEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: uuid.UUID
    citation_key: str
    editorial_version: int
    source_revision: str
    evidence_text: str
    total_characters: int
    offset: int
    next_offset: int | None


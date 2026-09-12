"""Explicit optimistic commands for human report editing and publication."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.reports import ReportSchema

_UNSUPPORTED_TEXT = re.compile("[\x00\ud800-\udfff]")


class EditorialTextSchema(ReportSchema):
    @field_validator("*", mode="after")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and _UNSUPPORTED_TEXT.search(value):
            raise ValueError("Text cannot contain NUL characters or invalid Unicode.")
        return value


class ReportSectionEdit(EditorialTextSchema):
    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    body_markdown: str = Field(max_length=400_000)


class ReportDraftUpdate(EditorialTextSchema):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=255)
    summary_text: str | None = Field(default=None, max_length=100_000)
    sections: list[ReportSectionEdit] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def bounded_document(self):
        if len({section.key for section in self.sections}) != len(self.sections):
            raise ValueError("Report section keys must be unique.")
        if (
            sum(len(section.body_markdown.encode("utf-8")) for section in self.sections)
            > 2_000_000
        ):
            raise ValueError("Edited report content must fit within two megabytes.")
        if not self.title.strip() or any(
            not section.title.strip() for section in self.sections
        ):
            raise ValueError("Report and section titles cannot be blank.")
        return self


class ReportEditorialTransition(EditorialTextSchema):
    expected_version: int = Field(ge=1)
    action: Literal["submit", "return_to_draft", "approve", "publish"]
    note: str | None = Field(default=None, max_length=2_000)

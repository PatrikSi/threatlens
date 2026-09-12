"""Validate user-authored text before PostgreSQL/JSON persistence."""

from pydantic import BaseModel, field_validator


def validate_storage_text(value: str) -> str:
    if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("Text contains a character that cannot be stored.")
    return value


class StorageTextInput(BaseModel):
    @field_validator("*")
    @classmethod
    def validate_text_fields(cls, value):
        return validate_storage_text(value) if isinstance(value, str) else value

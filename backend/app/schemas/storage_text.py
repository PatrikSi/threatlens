"""Validate user-authored text before PostgreSQL/JSON persistence."""

from typing import Annotated

from pydantic import AfterValidator


def validate_storage_text(value: str) -> str:
    if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("Text contains a character that cannot be stored.")
    return value


StorageText = Annotated[str, AfterValidator(validate_storage_text)]

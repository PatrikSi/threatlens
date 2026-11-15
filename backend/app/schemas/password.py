"""Admission for new credentials entered through browser password fields."""

from typing import Annotated

from pydantic import AfterValidator


def _validate_new_password(value: str) -> str:
    # Native password inputs discard CR/LF, so storing either would create a
    # credential the browser cannot submit unchanged. Existing credentials keep
    # their separate login and current-password admission for API compatibility.
    if "\r" in value or "\n" in value:
        raise ValueError("Password must not contain carriage returns or line feeds")
    return value


NewPassword = Annotated[str, AfterValidator(_validate_new_password)]

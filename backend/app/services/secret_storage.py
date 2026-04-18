from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_ENCRYPTED_JSON_KEY = "_threatlens_encrypted"
_ENCRYPTED_TEXT_PREFIX = "enc:v1:"


def encrypt_text(value: str | None) -> str | None:
    if value is None:
        return None
    token = _fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_ENCRYPTED_TEXT_PREFIX}{token}"


def decrypt_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(_ENCRYPTED_TEXT_PREFIX):
        return value
    token = value[len(_ENCRYPTED_TEXT_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt stored data") from exc


def encrypt_json(value: Any) -> dict[str, str]:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    encrypted = encrypt_text(payload)
    if encrypted is None:  # pragma: no cover - defensive only
        raise ValueError("Unable to encrypt empty payload")
    return {_ENCRYPTED_JSON_KEY: encrypted}


def decrypt_json(value: Any) -> Any:
    if not _is_encrypted_json(value):
        return value
    decrypted = decrypt_text(value[_ENCRYPTED_JSON_KEY])
    if decrypted is None:  # pragma: no cover - defensive only
        return None
    return json.loads(decrypted)


def is_encrypted_json(value: Any) -> bool:
    return _is_encrypted_json(value)


def _is_encrypted_json(value: Any) -> bool:
    return isinstance(value, dict) and set(value.keys()) == {_ENCRYPTED_JSON_KEY} and isinstance(value[_ENCRYPTED_JSON_KEY], str)


def _fernet() -> Fernet:
    secret = (get_settings().jwt_secret or "change-me").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)

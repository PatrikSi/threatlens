from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings, get_settings

_ENCRYPTED_JSON_KEY = "_threatlens_encrypted"
_ENCRYPTED_TEXT_PREFIX = "enc:v1:"
_KEYED_DIGEST_PREFIX = "hmac:v1:"


def encrypt_text(value: str | None) -> str | None:
    if value is None:
        return None
    token = _encryption_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_ENCRYPTED_TEXT_PREFIX}{token}"


def decrypt_text(value: str | None) -> str | None:
    plaintext, _needs_rotation = decrypt_text_with_rotation(value)
    return plaintext


def decrypt_text_with_rotation(value: str | None) -> tuple[str | None, bool]:
    """Decrypt text and report whether it should be re-encrypted with the active key."""
    if value is None:
        return None, False
    if not value.startswith(_ENCRYPTED_TEXT_PREFIX):
        return value, True
    token = value[len(_ENCRYPTED_TEXT_PREFIX) :]
    for index, fernet in enumerate(_decryption_fernets()):
        try:
            return (
                fernet.decrypt(token.encode("utf-8")).decode("utf-8"),
                index > 0,
            )
        except InvalidToken:
            continue
    raise ValueError("Unable to decrypt stored data")


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


def is_encrypted_text(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(_ENCRYPTED_TEXT_PREFIX)


def encrypt_text_if_legacy(value: str | None) -> tuple[str | None, bool]:
    if value is None or is_encrypted_text(value):
        return value, False
    return encrypt_text(value), True


def encrypt_json_if_legacy(value: Any) -> tuple[Any, bool]:
    if value is None or _is_encrypted_json(value):
        return value, False
    return encrypt_json(value), True


def keyed_hexdigest(value: str | None, *, purpose: str) -> str | None:
    if value is None:
        return None
    secret = _hashing_secrets()[0]
    payload = f"{purpose}\x00{value}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def keyed_hexdigest_candidates(value: str | None, *, purpose: str) -> tuple[str, ...]:
    """Return current and rotation-fallback digests for a stored keyed value."""
    if value is None:
        return ()
    payload = f"{purpose}\x00{value}".encode("utf-8")
    return tuple(
        hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        for secret in _hashing_secrets()
    )


def versioned_keyed_hexdigest(value: str | None, *, purpose: str) -> str | None:
    if value is None:
        return None
    secret = _hashing_secrets()[0]
    digest = _keyed_digest(secret, value=value, purpose=purpose)
    return f"{_KEYED_DIGEST_PREFIX}{_hashing_key_id(secret)}:{digest}"


def versioned_keyed_hexdigest_candidates(
    value: str | None, *, purpose: str
) -> tuple[str, ...]:
    """Return versioned and legacy candidates for online hash migration."""

    if value is None:
        return ()
    candidates: list[str] = []
    for secret in _hashing_secrets():
        digest = _keyed_digest(secret, value=value, purpose=purpose)
        candidates.extend(
            (
                f"{_KEYED_DIGEST_PREFIX}{_hashing_key_id(secret)}:{digest}",
                digest,
            )
        )
    return tuple(candidates)


def configured_hashing_key_ids(
    settings: Settings | None = None,
) -> tuple[str, tuple[str, ...]]:
    if settings is None:
        secrets = _hashing_secrets()
    else:
        candidates = [
            (settings.app_data_encryption_key or "").strip(),
            *(
                secret.strip()
                for secret in settings.app_data_encryption_previous_keys
            ),
        ]
        secrets = list(dict.fromkeys(secret for secret in candidates if secret))
        if not secrets:
            raise ValueError(
                "app_data_encryption_key must be configured before inventorying keyed hashes"
            )
    return _hashing_key_id(secrets[0]), tuple(
        _hashing_key_id(secret) for secret in secrets[1:]
    )


def stored_keyed_digest_key_id(value: str) -> str | None:
    if not value.startswith(_KEYED_DIGEST_PREFIX):
        return None
    remainder = value[len(_KEYED_DIGEST_PREFIX) :]
    key_id, separator, digest = remainder.partition(":")
    if (
        not separator
        or len(key_id) != 16
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in key_id + digest)
    ):
        return "invalid"
    return key_id


def _is_encrypted_json(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value.keys()) == {_ENCRYPTED_JSON_KEY}
        and isinstance(value[_ENCRYPTED_JSON_KEY], str)
    )


def _encryption_fernet() -> Fernet:
    settings = get_settings()
    secret = settings.app_data_encryption_key
    if not secret:
        raise ValueError(
            "app_data_encryption_key must be configured before encrypting stored data"
        )
    return _build_fernet(secret)


def _decryption_fernets() -> list[Fernet]:
    settings = get_settings()
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(secret: str | None) -> None:
        normalized = (secret or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    _append(settings.app_data_encryption_key)
    for previous_key in settings.app_data_encryption_previous_keys:
        _append(previous_key)

    return [_build_fernet(secret) for secret in candidates]


def _hashing_secrets() -> list[str]:
    settings = get_settings()
    candidates = [
        (settings.app_data_encryption_key or "").strip(),
        *(secret.strip() for secret in settings.app_data_encryption_previous_keys),
    ]
    secrets = list(dict.fromkeys(secret for secret in candidates if secret))
    if not secrets:
        raise ValueError(
            "app_data_encryption_key must be configured before hashing stored data"
        )
    return secrets


def _keyed_digest(secret: str, *, value: str, purpose: str) -> str:
    payload = f"{purpose}\x00{value}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _hashing_key_id(secret: str) -> str:
    return hashlib.sha256(
        f"threatlens:hmac-key-id:v1\x00{secret}".encode("utf-8")
    ).hexdigest()[:16]


def _build_fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)

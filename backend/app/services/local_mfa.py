from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pyotp
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.mfa import MFALoginChallenge, UserRecoveryCode, UserTOTPCredential
from app.models.user import User
from app.services.secret_storage import (
    decrypt_text_with_rotation,
    encrypt_text,
    versioned_keyed_hexdigest,
    versioned_keyed_hexdigest_candidates,
)

MFA_CHALLENGE_MARKER = "tlm"
TOTP_INTERVAL_SECONDS = 30
TOTP_VALID_WINDOW_STEPS = 1
RECOVERY_CODE_COUNT = 10
MAX_USER_AGENT_CHARS = 512
MAX_CLIENT_IP_CHARS = 64


class MFAError(RuntimeError):
    pass


class MFAConflictError(MFAError):
    pass


class MFAEnrollmentExpiredError(MFAConflictError):
    pass


class MFAInvalidCodeError(MFAError):
    def __init__(
        self,
        message: str,
        *,
        user_id: uuid.UUID | None = None,
        attempts_remaining: int | None = None,
    ) -> None:
        super().__init__(message)
        self.user_id = user_id
        self.attempts_remaining = attempts_remaining


class MFAChallengeError(MFAError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "mfa_challenge_invalid_or_expired",
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MFAEnrollment:
    credential: UserTOTPCredential
    secret: str
    provisioning_uri: str


@dataclass(frozen=True)
class MFAConfirmation:
    credential: UserTOTPCredential
    recovery_codes: list[str]


@dataclass(frozen=True)
class MFAVerification:
    method: str
    recovery_codes_remaining: int


@dataclass(frozen=True)
class CreatedMFAChallenge:
    token: str
    challenge: MFALoginChallenge


def start_totp_enrollment(
    db: Session,
    *,
    user: User,
    enrollment_session_id: uuid.UUID,
    enrollment_auth_token_version: int,
    issuer: str = "ThreatLens",
) -> MFAEnrollment:
    credential = db.scalar(
        select(UserTOTPCredential)
        .where(UserTOTPCredential.user_id == user.id)
        .with_for_update()
    )
    if credential is not None and credential.status == "active":
        raise MFAConflictError(
            "Authenticator app verification is already enabled for this account."
        )
    secret = pyotp.random_base32(length=32)
    encrypted_secret = encrypt_text(secret)
    if encrypted_secret is None:
        raise MFAError("The authenticator secret could not be protected for storage.")
    if credential is None:
        credential = UserTOTPCredential(
            user_id=user.id,
            secret_encrypted=encrypted_secret,
            status="pending",
            enrollment_session_id=enrollment_session_id,
            enrollment_auth_token_version=enrollment_auth_token_version,
        )
        db.add(credential)
    else:
        credential.secret_encrypted = encrypted_secret
        credential.last_accepted_step = None
        credential.confirmed_at = None
        credential.recovery_codes_generated_at = None
        credential.enrollment_session_id = enrollment_session_id
        credential.enrollment_auth_token_version = enrollment_auth_token_version
    db.flush()
    db.execute(
        delete(UserRecoveryCode).where(UserRecoveryCode.credential_id == credential.id)
    )
    provisioning_uri = pyotp.TOTP(
        secret, interval=TOTP_INTERVAL_SECONDS
    ).provisioning_uri(
        name=user.email,
        issuer_name=issuer,
    )
    return MFAEnrollment(
        credential=credential, secret=secret, provisioning_uri=provisioning_uri
    )


def confirm_totp_enrollment(
    db: Session,
    *,
    user_id: uuid.UUID,
    code: str,
    enrollment_session_id: uuid.UUID,
    enrollment_auth_token_version: int,
    now: datetime | None = None,
) -> MFAConfirmation:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    credential = _locked_credential(db, user_id=user_id, require_active=False)
    if credential.status != "pending":
        raise MFAConflictError("Authenticator enrollment is already confirmed.")
    if (
        credential.enrollment_session_id != enrollment_session_id
        or credential.enrollment_auth_token_version
        != enrollment_auth_token_version
    ):
        db.delete(credential)
        db.flush()
        raise MFAEnrollmentExpiredError(
            "Account security or the initiating browser session changed. Sign in again and restart authenticator enrollment."
        )
    enrollment_started_at = credential.updated_at or credential.created_at
    if (
        _as_utc(enrollment_started_at)
        + timedelta(seconds=get_settings().auth_mfa_pending_enrollment_ttl_seconds)
        <= current_time
    ):
        db.delete(credential)
        db.flush()
        raise MFAEnrollmentExpiredError(
            "Authenticator enrollment expired. Start a new enrollment and scan the new secret."
        )
    accepted_step = _verify_totp_step(credential, code=code, now=current_time)
    credential.status = "active"
    credential.confirmed_at = current_time
    credential.last_used_at = current_time
    credential.last_accepted_step = accepted_step
    credential.enrollment_session_id = None
    credential.enrollment_auth_token_version = None
    recovery_codes = _replace_recovery_codes(
        db, credential=credential, now=current_time
    )
    db.add(credential)
    db.flush()
    return MFAConfirmation(credential=credential, recovery_codes=recovery_codes)


def verify_active_mfa_code(
    db: Session,
    *,
    user_id: uuid.UUID,
    code: str,
    now: datetime | None = None,
) -> MFAVerification:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    credential = _locked_credential(db, user_id=user_id, require_active=True)
    normalized_totp = _normalize_totp(code)
    if normalized_totp is not None:
        accepted_step = _verify_totp_step(
            credential, code=normalized_totp, now=current_time
        )
        credential.last_accepted_step = accepted_step
        credential.last_used_at = current_time
        db.add(credential)
        remaining = _unused_recovery_code_count(db, credential.id)
        db.flush()
        return MFAVerification(method="totp", recovery_codes_remaining=remaining)

    normalized_recovery = _normalize_recovery_code(code)
    code_hashes = versioned_keyed_hexdigest_candidates(
        normalized_recovery,
        purpose=f"mfa-recovery:{credential.id}",
    )
    recovery = db.scalar(
        select(UserRecoveryCode)
        .where(
            UserRecoveryCode.credential_id == credential.id,
            UserRecoveryCode.code_hash.in_(code_hashes),
            UserRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if recovery is None:
        raise MFAInvalidCodeError(
            "The authenticator or recovery code is invalid or has already been used."
        )
    recovery.used_at = current_time
    current_code_hash = versioned_keyed_hexdigest(
        normalized_recovery,
        purpose=f"mfa-recovery:{credential.id}",
    )
    if current_code_hash is not None and recovery.code_hash != current_code_hash:
        recovery.code_hash = current_code_hash
    credential.last_used_at = current_time
    db.add_all([recovery, credential])
    db.flush()
    return MFAVerification(
        method="recovery_code",
        recovery_codes_remaining=_unused_recovery_code_count(db, credential.id),
    )


def verify_active_totp_code(
    db: Session,
    *,
    user_id: uuid.UUID,
    code: str,
    now: datetime | None = None,
) -> MFAVerification:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    credential = _locked_credential(db, user_id=user_id, require_active=True)
    normalized_totp = _normalize_totp(code)
    if normalized_totp is None:
        raise MFAInvalidCodeError(
            "Enter a valid 6-digit authenticator code. Recovery codes cannot authorize replacement of all recovery codes."
        )
    accepted_step = _verify_totp_step(
        credential,
        code=normalized_totp,
        now=current_time,
    )
    credential.last_accepted_step = accepted_step
    credential.last_used_at = current_time
    db.add(credential)
    remaining = _unused_recovery_code_count(db, credential.id)
    db.flush()
    return MFAVerification(method="totp", recovery_codes_remaining=remaining)


def regenerate_recovery_codes(
    db: Session,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> MFAConfirmation:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    credential = _locked_credential(db, user_id=user_id, require_active=True)
    recovery_codes = _replace_recovery_codes(
        db, credential=credential, now=current_time
    )
    return MFAConfirmation(credential=credential, recovery_codes=recovery_codes)


def disable_totp(db: Session, *, user_id: uuid.UUID) -> bool:
    credential = db.scalar(
        select(UserTOTPCredential)
        .where(UserTOTPCredential.user_id == user_id)
        .with_for_update()
    )
    if credential is None:
        return False
    db.delete(credential)
    db.flush()
    return True


def cancel_pending_totp_enrollment(db: Session, *, user_id: uuid.UUID) -> bool:
    result = db.execute(
        delete(UserTOTPCredential).where(
            UserTOTPCredential.user_id == user_id,
            UserTOTPCredential.status == "pending",
        )
    )
    return bool(result.rowcount)


def mfa_status(db: Session, *, user_id: uuid.UUID) -> tuple[bool, datetime | None, int]:
    credential = db.scalar(
        select(UserTOTPCredential).where(
            UserTOTPCredential.user_id == user_id,
            UserTOTPCredential.status == "active",
        )
    )
    if credential is None:
        return False, None, 0
    return True, credential.confirmed_at, _unused_recovery_code_count(db, credential.id)


def create_mfa_challenge(
    db: Session,
    *,
    user_id: uuid.UUID,
    auth_token_version: int,
    client_ip: str | None,
    user_agent: str | None,
    now: datetime | None = None,
) -> CreatedMFAChallenge:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    settings = get_settings()
    db.execute(
        update(MFALoginChallenge)
        .where(
            MFALoginChallenge.user_id == user_id,
            MFALoginChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=current_time)
    )
    challenge_id = uuid.uuid4()
    token = f"{MFA_CHALLENGE_MARKER}_{challenge_id.hex}_{secrets.token_urlsafe(32)}"
    challenge = MFALoginChallenge(
        id=challenge_id,
        user_id=user_id,
        token_hash=_token_hash(token),
        auth_token_version=int(auth_token_version),
        attempt_count=0,
        max_attempts=settings.auth_mfa_challenge_max_attempts,
        password_authenticated_at=current_time,
        expires_at=current_time
        + timedelta(seconds=settings.auth_mfa_challenge_ttl_seconds),
        client_ip=_bounded(client_ip, MAX_CLIENT_IP_CHARS),
        user_agent=_bounded(user_agent, MAX_USER_AGENT_CHARS),
    )
    db.add(challenge)
    db.flush()
    return CreatedMFAChallenge(token=token, challenge=challenge)


def consume_mfa_challenge(
    db: Session,
    *,
    token: str,
    code: str,
    now: datetime | None = None,
) -> tuple[MFALoginChallenge, MFAVerification]:
    current_time = _as_utc(now or datetime.now(timezone.utc))
    challenge_id = _extract_challenge_id(token)
    if challenge_id is None:
        raise MFAChallengeError(
            "The MFA sign-in challenge is invalid or expired. Start sign-in again."
        )
    challenge_preview = db.execute(
        select(MFALoginChallenge.user_id, MFALoginChallenge.token_hash).where(
            MFALoginChallenge.id == challenge_id
        )
    ).one_or_none()
    if challenge_preview is None or not hmac.compare_digest(
        challenge_preview.token_hash, _token_hash(token)
    ):
        raise MFAChallengeError(
            "The MFA sign-in challenge is invalid or expired. Start sign-in again."
        )
    user = db.scalar(
        select(User)
        .where(User.id == challenge_preview.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    challenge = db.scalar(
        select(MFALoginChallenge)
        .where(MFALoginChallenge.id == challenge_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if challenge is None or not hmac.compare_digest(
        challenge.token_hash, _token_hash(token)
    ):
        raise MFAChallengeError(
            "The MFA sign-in challenge is invalid or expired. Start sign-in again."
        )
    if challenge.consumed_at is not None:
        raise MFAChallengeError(
            "The MFA sign-in challenge is invalid or expired. Start sign-in again."
        )
    if _as_utc(challenge.expires_at) <= current_time:
        challenge.consumed_at = current_time
        db.add(challenge)
        db.flush()
        raise MFAChallengeError(
            "The MFA sign-in challenge is invalid or expired. Start sign-in again.",
            code="mfa_challenge_expired",
        )
    if challenge.attempt_count >= challenge.max_attempts:
        challenge.consumed_at = current_time
        db.flush()
        raise MFAChallengeError(
            "Too many MFA attempts. Start sign-in again.",
            code="mfa_challenge_attempts_exhausted",
        )
    if user is None or challenge.auth_token_version != int(
        user.auth_token_version or 0
    ):
        challenge.consumed_at = current_time
        db.add(challenge)
        db.flush()
        raise MFAChallengeError(
            "Account security changed after password verification. Start sign-in again.",
            code="mfa_challenge_security_changed",
        )
    try:
        verification = verify_active_mfa_code(
            db,
            user_id=challenge.user_id,
            code=code,
            now=current_time,
        )
    except MFAInvalidCodeError as exc:
        challenge.attempt_count += 1
        if challenge.attempt_count >= challenge.max_attempts:
            challenge.consumed_at = current_time
        db.add(challenge)
        db.flush()
        raise MFAInvalidCodeError(
            str(exc),
            user_id=challenge.user_id,
            attempts_remaining=max(0, challenge.max_attempts - challenge.attempt_count),
        ) from exc
    challenge.consumed_at = current_time
    db.add(challenge)
    db.flush()
    return challenge, verification


def invalidate_mfa_challenge(
    db: Session,
    *,
    token: str,
    now: datetime | None = None,
) -> bool:
    challenge_id = _extract_challenge_id(token)
    if challenge_id is None:
        return False
    current_time = _as_utc(now or datetime.now(timezone.utc))
    result = db.execute(
        update(MFALoginChallenge)
        .where(
            MFALoginChallenge.id == challenge_id,
            MFALoginChallenge.token_hash == _token_hash(token),
            MFALoginChallenge.consumed_at.is_(None),
        )
        .values(consumed_at=current_time)
    )
    return bool(result.rowcount)


def cleanup_mfa_challenges(
    db: Session,
    *,
    now: datetime | None = None,
    retention_hours: int = 24,
    limit: int = 1_000,
) -> int:
    cutoff = _as_utc(now or datetime.now(timezone.utc)) - timedelta(
        hours=max(1, retention_hours)
    )
    challenge_ids = list(
        db.scalars(
            select(MFALoginChallenge.id)
            .where(MFALoginChallenge.created_at < cutoff)
            .order_by(MFALoginChallenge.created_at.asc())
            .limit(max(1, min(limit, 10_000)))
            .with_for_update(skip_locked=True)
        ).all()
    )
    if not challenge_ids:
        return 0
    result = db.execute(
        delete(MFALoginChallenge).where(
            MFALoginChallenge.id.in_(challenge_ids),
            MFALoginChallenge.created_at < cutoff,
        )
    )
    return int(result.rowcount or 0)


def cleanup_pending_totp_enrollments(
    db: Session,
    *,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
    limit: int = 1_000,
) -> int:
    effective_ttl = (
        ttl_seconds or get_settings().auth_mfa_pending_enrollment_ttl_seconds
    )
    cutoff = _as_utc(now or datetime.now(timezone.utc)) - timedelta(
        seconds=max(1, effective_ttl)
    )
    credential_ids = list(
        db.scalars(
            select(UserTOTPCredential.id)
            .where(
                UserTOTPCredential.status == "pending",
                UserTOTPCredential.updated_at < cutoff,
            )
            .order_by(UserTOTPCredential.updated_at.asc(), UserTOTPCredential.id)
            .limit(max(1, min(limit, 10_000)))
            .with_for_update(skip_locked=True)
        ).all()
    )
    if not credential_ids:
        return 0
    result = db.execute(
        delete(UserTOTPCredential).where(
            UserTOTPCredential.id.in_(credential_ids),
            UserTOTPCredential.status == "pending",
            UserTOTPCredential.updated_at < cutoff,
        )
    )
    return int(result.rowcount or 0)


def _locked_credential(
    db: Session, *, user_id: uuid.UUID, require_active: bool
) -> UserTOTPCredential:
    filters = [UserTOTPCredential.user_id == user_id]
    if require_active:
        filters.append(UserTOTPCredential.status == "active")
    credential = db.scalar(select(UserTOTPCredential).where(*filters).with_for_update())
    if credential is None:
        raise MFAConflictError(
            "Authenticator app verification is not enabled for this account."
        )
    return credential


def _verify_totp_step(
    credential: UserTOTPCredential, *, code: str, now: datetime
) -> int:
    normalized = _normalize_totp(code)
    if normalized is None:
        raise MFAInvalidCodeError("Enter a valid 6-digit authenticator code.")
    try:
        secret, secret_needs_rotation = decrypt_text_with_rotation(
            credential.secret_encrypted
        )
    except ValueError as exc:
        raise MFAError(
            "The stored authenticator credential cannot be decrypted. Contact an administrator."
        ) from exc
    if not secret:
        raise MFAError(
            "The stored authenticator credential is empty. Contact an administrator."
        )
    current_step = int(now.timestamp()) // TOTP_INTERVAL_SECONDS
    totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL_SECONDS)
    accepted_step = None
    for candidate_step in range(
        current_step - TOTP_VALID_WINDOW_STEPS,
        current_step + TOTP_VALID_WINDOW_STEPS + 1,
    ):
        if hmac.compare_digest(
            totp.at(candidate_step * TOTP_INTERVAL_SECONDS), normalized
        ):
            accepted_step = candidate_step
            break
    if accepted_step is None:
        raise MFAInvalidCodeError(
            "The authenticator or recovery code is invalid or has already been used."
        )
    if secret_needs_rotation:
        rotated_secret = encrypt_text(secret)
        if rotated_secret is None:  # pragma: no cover - secret is non-empty above
            raise MFAError("The authenticator credential could not be rotated.")
        credential.secret_encrypted = rotated_secret
    if (
        credential.last_accepted_step is not None
        and accepted_step <= credential.last_accepted_step
    ):
        raise MFAInvalidCodeError(
            "The authenticator or recovery code is invalid or has already been used."
        )
    return accepted_step


def _replace_recovery_codes(
    db: Session,
    *,
    credential: UserTOTPCredential,
    now: datetime,
) -> list[str]:
    db.execute(
        delete(UserRecoveryCode).where(UserRecoveryCode.credential_id == credential.id)
    )
    plaintext_codes = [_generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    db.add_all(
        [
            UserRecoveryCode(
                credential_id=credential.id,
                ordinal=index,
                code_hash=versioned_keyed_hexdigest(
                    _normalize_recovery_code(code),
                    purpose=f"mfa-recovery:{credential.id}",
                ),
            )
            for index, code in enumerate(plaintext_codes, start=1)
        ]
    )
    credential.recovery_codes_generated_at = now
    db.add(credential)
    db.flush()
    return plaintext_codes


def _unused_recovery_code_count(db: Session, credential_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count(UserRecoveryCode.id)).where(
                UserRecoveryCode.credential_id == credential_id,
                UserRecoveryCode.used_at.is_(None),
            )
        )
        or 0
    )


def _generate_recovery_code() -> str:
    raw = secrets.token_hex(10).upper()
    return "-".join(raw[index : index + 5] for index in range(0, len(raw), 5))


def _normalize_totp(value: str) -> str | None:
    normalized = "".join(value.strip().split())
    return (
        normalized
        if len(normalized) == 6 and normalized.isascii() and normalized.isdigit()
        else None
    )


def _normalize_recovery_code(value: str) -> str:
    return "".join(
        character
        for character in value.upper()
        if character.isascii() and character.isalnum()
    )


def _extract_challenge_id(token: str) -> uuid.UUID | None:
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != MFA_CHALLENGE_MARKER or not parts[2]:
        return None
    try:
        return uuid.UUID(hex=parts[1])
    except (AttributeError, ValueError):
        return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bounded(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized[:limit] or None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

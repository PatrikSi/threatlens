import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, resolve_client_ip
from app.core.config import get_settings
from app.core.security import (
    clear_auth_cookies,
    create_access_token,
    decode_access_token_claims,
    generate_csrf_token,
    get_password_hash,
    set_auth_cookies,
    verify_password,
)
from app.models.api_token import ApiToken
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    AppFeaturesResponse,
    ChangePasswordRequest,
    CurrentUserResponse,
    LoginRequest,
    RegisterRequest,
    RegistrationSettingsResponse,
    TokenResponse,
    UserResponse,
)
from app.services.audit import record_audit
from app.services.auth_rate_limit import (
    check_password_verification_throttle,
    check_login_throttle,
    clear_password_verification_failures,
    clear_login_failures,
    record_password_verification_failure,
    record_login_failure,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _resolve_app_features(db: Session | None = None) -> AppFeaturesResponse:
    settings = get_settings()
    if not settings.ai_enabled:
        return AppFeaturesResponse(
            ai_enabled=False,
            ai_configured=False,
            ai_summary_enabled=False,
            ai_relevance_enabled=False,
            ai_daily_brief_enabled=False,
        )

    ai_summary_enabled = True
    ai_relevance_enabled = True
    ai_daily_brief_enabled = True
    ai_configured = False
    if db is not None:
        from app.services.ai_config import load_public_ai_feature_flags

        flags = load_public_ai_feature_flags(db)
        ai_summary_enabled = flags.ai_summary_enabled
        ai_relevance_enabled = flags.ai_relevance_enabled
        ai_daily_brief_enabled = flags.ai_daily_brief_enabled
        ai_configured = flags.ai_configured

    return AppFeaturesResponse(
        ai_enabled=True,
        ai_configured=ai_configured,
        ai_summary_enabled=ai_summary_enabled,
        ai_relevance_enabled=ai_relevance_enabled,
        ai_daily_brief_enabled=ai_daily_brief_enabled,
    )


@router.get("/registration-settings", response_model=RegistrationSettingsResponse)
def registration_settings():
    settings = get_settings()
    return RegistrationSettingsResponse(
        allow_self_registration=settings.allow_self_registration,
        ai_enabled=settings.ai_enabled,
    )


@router.post("/register", response_model=UserResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.allow_self_registration:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Self-registration is disabled")

    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    user = User(
        email=payload.email.lower(),
        password_hash=get_password_hash(payload.password),
        is_active=True,
        is_approved=False,
        approved_at=None,
    )
    db.add(user)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.register",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"email": user.email, "is_approved": user.is_approved},
    )
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Create a browser session cookie and return only cookie-session metadata.

    Scoped API access should use dedicated API tokens in the `Authorization` header.
    Browser clients should rely on the HttpOnly session cookie set by this route.
    """
    email = payload.email.lower()
    client_ip = resolve_client_ip(request)
    throttle = check_login_throttle(email, client_ip)
    if throttle.blocked:
        detail = "Too many failed login attempts. Try again later."
        headers = {"Retry-After": str(throttle.retry_after_seconds)} if throttle.retry_after_seconds else None
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers=headers)

    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        record_login_failure(email, client_ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval.",
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")

    clear_login_failures(email, client_ip)
    token = create_access_token(str(user.id), token_version=int(user.auth_token_version or 0))
    csrf_token = generate_csrf_token()
    set_auth_cookies(response, token, csrf_token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"email": user.email},
    )
    db.commit()
    return TokenResponse(csrf_token=csrf_token)


@router.get("/me", response_model=CurrentUserResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        is_approved=user.is_approved,
        approved_at=user.approved_at,
        created_at=user.created_at,
        features=_resolve_app_features(db),
    )


@router.post("/change-password", status_code=status.HTTP_200_OK)
def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _verify_current_password_or_throttle(request=request, user=user, candidate_password=payload.current_password)

    revoked_at = datetime.now(timezone.utc)
    revoked_api_tokens = db.execute(
        update(ApiToken)
        .where(
            ApiToken.user_id == user.id,
            ApiToken.revoked_at.is_(None),
        )
        .values(revoked_at=revoked_at)
    ).rowcount or 0
    user.password_hash = get_password_hash(payload.new_password)
    user.auth_token_version = int(user.auth_token_version or 0) + 1
    db.add(user)
    record_audit(
        db,
        actor_user_id=user.id,
        action="auth.change_password",
        resource_type="user",
        resource_id=str(user.id),
        metadata={"revoked_api_tokens": int(revoked_api_tokens)},
    )
    db.commit()
    return {"status": "ok"}


def _verify_current_password_or_throttle(*, request: Request, user: User, candidate_password: str) -> None:
    client_ip = resolve_client_ip(request)
    throttle = check_password_verification_throttle(user.email, client_ip)
    if throttle.blocked:
        detail = "Too many failed current password verification attempts. Try again later."
        headers = {"Retry-After": str(throttle.retry_after_seconds)} if throttle.retry_after_seconds else None
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers=headers)

    if not verify_password(candidate_password, user.password_hash):
        record_password_verification_failure(user.email, client_ip)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    clear_password_verification_failures(user.email, client_ip)


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    auth_cookie = request.cookies.get(settings.auth_cookie_name)
    if auth_cookie and settings.auth_require_csrf:
        csrf_cookie = request.cookies.get(settings.auth_csrf_cookie_name)
        csrf_header = request.headers.get(settings.auth_csrf_header_name)
        if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token")

    user = _resolve_logout_session_user(db, auth_cookie)
    if user is not None:
        user.auth_token_version = int(user.auth_token_version or 0) + 1
        db.add(user)
        record_audit(
            db,
            actor_user_id=user.id,
            action="auth.logout",
            resource_type="user",
            resource_id=str(user.id),
            metadata={"session_revoked": True},
        )
        db.commit()

    clear_auth_cookies(response)
    return {"status": "ok"}


def _resolve_logout_session_user(db: Session, auth_cookie: str | None) -> User | None:
    if not auth_cookie:
        return None

    claims = decode_access_token_claims(auth_cookie)
    if claims is None:
        return None

    subject = claims.get("sub")
    if not subject:
        return None

    try:
        user_id = uuid.UUID(subject)
        token_version = int(claims.get("ver", 0))
    except (TypeError, ValueError):
        return None

    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        return None
    if token_version != int(user.auth_token_version or 0):
        return None
    return user

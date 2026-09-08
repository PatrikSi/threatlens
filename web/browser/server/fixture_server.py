"""Disposable fixture server. Never mounted or imported by the production app."""

from __future__ import annotations

import argparse
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import html
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode
import uuid

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy import text, update
import uvicorn

from app.core.security import get_password_hash
from app.db.session import SessionLocal, engine
from app.main import app as application
from app.models.auth_session import AuthSession
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.oidc import OIDCProvider
from app.models.user import User


ROOT = Path(__file__).resolve().parents[3]
ORIGIN = os.environ["THREATLENS_BROWSER_API_ORIGIN"]
PUBLIC_ORIGIN = os.environ["THREATLENS_BROWSER_BASE_URL"]
CONTROL_TOKEN = os.environ["THREATLENS_BROWSER_CONTROL_TOKEN"]
ISSUER = f"{ORIGIN}/__idp__"
PASSWORD = "Browser-only-password-729!"
key = RSAKey.generate_key(2048)
other_key = RSAKey.generate_key(2048)
codes: dict[str, dict] = {}
tokens: dict[str, dict] = {}
state = {
    "mode": "valid",
    "subject": "",
    "token_calls": 0,
    "jwks_calls": 0,
    "userinfo_calls": 0,
    "pkce_validated": 0,
    "auth_unavailable": False,
}


@asynccontextmanager
async def lifespan(_app):
    async with application.router.lifespan_context(application):
        yield


harness = FastAPI(lifespan=lifespan)


def require_control(request: Request):
    if not hmac.compare_digest(
        request.headers.get("x-browser-control", ""), CONTROL_TOKEN
    ):
        raise HTTPException(403, "Invalid isolated-test control token")


@harness.get("/__browser__/ready")
def ready():
    return {"ready": True}


@harness.post("/__browser__/users", dependencies=[Depends(require_control)])
def create_user():
    with SessionLocal.begin() as db:
        user = User(
            email=f"browser-{uuid.uuid4().hex}@example.com",
            password_hash=get_password_hash(PASSWORD),
            role="admin",
            is_active=True,
            is_approved=True,
        )
        db.add(user)
        db.flush()
        return {"id": str(user.id), "email": user.email, "password": PASSWORD}


@harness.post("/__browser__/expire", dependencies=[Depends(require_control)])
async def expire(request: Request):
    payload = await request.json()
    with SessionLocal.begin() as db:
        result = db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == uuid.UUID(payload["userId"]))
            .values(
                absolute_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
            )
        )
        return {"expired": result.rowcount}


@harness.post("/__browser__/outage", dependencies=[Depends(require_control)])
async def outage(request: Request):
    state["auth_unavailable"] = bool((await request.json())["unavailable"])
    return {"unavailable": state["auth_unavailable"]}


@harness.post("/__browser__/idp", dependencies=[Depends(require_control)])
async def configure_idp(request: Request):
    mode = (await request.json()).get("mode", "valid")
    if mode not in {"valid", "bad_nonce", "bad_signature", "token_unavailable"}:
        raise HTTPException(422, "Unknown fixture mode")
    codes.clear()
    tokens.clear()
    state.update(
        mode=mode,
        subject=uuid.uuid4().hex,
        token_calls=0,
        jwks_calls=0,
        userinfo_calls=0,
        pkce_validated=0,
    )
    return {"email": f"oidc-{state['subject']}@example.com"}


@harness.get("/__browser__/idp", dependencies=[Depends(require_control)])
def idp_state():
    return {**state, "unconsumed_codes": len(codes)}


@harness.get("/__idp__/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    }


@harness.get("/__idp__/authorize", response_class=HTMLResponse)
def authorize(request: Request):
    query = dict(request.query_params)
    if (
        query.get("client_id") != "browser-client"
        or query.get("redirect_uri") != f"{PUBLIC_ORIGIN}/api/v1/auth/oidc/callback"
        or query.get("code_challenge_method") != "S256"
        or not query.get("state")
        or not query.get("nonce")
    ):
        raise HTTPException(400, "Invalid browser OIDC authorization request")
    code = secrets.token_urlsafe(24)
    codes[code] = {**query, "mode": state["mode"], "subject": state["subject"]}
    target = (
        f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"
    )
    return f'<!doctype html><html lang="en"><title>Isolated identity provider</title><main><h1>Isolated identity provider</h1><p>Synthetic identity for browser tests.</p><a href="{html.escape(target, quote=True)}">Continue to ThreatLens</a></main></html>'


@harness.post("/__idp__/token")
async def token(request: Request):
    from urllib.parse import parse_qs

    state["token_calls"] += 1
    data = {
        name: values[0]
        for name, values in parse_qs((await request.body()).decode()).items()
    }
    grant = codes.pop(data.get("code", ""), None)
    if grant is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(data.get("code_verifier", "").encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    if (
        not hmac.compare_digest(challenge, grant["code_challenge"])
        or data.get("redirect_uri") != grant["redirect_uri"]
        or data.get("client_id") != "browser-client"
        or data.get("grant_type") != "authorization_code"
    ):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    state["pkce_validated"] += 1
    if grant["mode"] == "token_unavailable":
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
    access_token = secrets.token_urlsafe(24)
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "browser-client",
        "sub": grant["subject"],
        "iat": now,
        "exp": now + 120,
        "auth_time": now,
        "nonce": "wrong-nonce" if grant["mode"] == "bad_nonce" else grant["nonce"],
        "email": f"oidc-{grant['subject']}@example.com",
        "email_verified": True,
    }
    tokens[access_token] = claims
    signed = jwt.encode(
        {"alg": "RS256", "kid": "browser-key"},
        claims,
        other_key if grant["mode"] == "bad_signature" else key,
    )
    return {
        "token_type": "Bearer",
        "access_token": access_token,
        "id_token": signed,
        "expires_in": 120,
    }


@harness.get("/__idp__/jwks")
def jwks():
    state["jwks_calls"] += 1
    return {
        "keys": [
            {
                **key.as_dict(private=False),
                "kid": "browser-key",
                "use": "sig",
                "alg": "RS256",
            }
        ]
    }


@harness.get("/__idp__/userinfo")
def userinfo(request: Request):
    state["userinfo_calls"] += 1
    claims = tokens.get(
        request.headers.get("authorization", "").removeprefix("Bearer ")
    )
    if claims is None:
        raise HTTPException(401, "Unknown fixture access token")
    return {name: claims[name] for name in ("sub", "email", "email_verified")}


class ControlledApplication:
    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["path"] == "/v1/auth/me"
            and state["auth_unavailable"]
        ):
            await JSONResponse(
                {"detail": "Isolated session verification outage"}, status_code=503
            )(scope, receive, send)
        else:
            await application(scope, receive, send)


harness.mount("/", ControlledApplication())


def initialize():
    deadline = time.monotonic() + 45
    while True:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            break
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    command.upgrade(config, "head")
    with SessionLocal.begin() as db:
        db.add(
            OIDCProvider(
                name="Browser SSO",
                enabled=True,
                issuer_url=ISSUER,
                client_id="browser-client",
                client_auth_method="none",
                public_base_url=PUBLIC_ORIGIN,
                scopes=["openid", "profile", "email"],
                default_role="viewer",
                jit_provisioning_enabled=True,
                auto_approve_users=True,
                require_verified_email=True,
                sync_roles_on_login=True,
            )
        )
        db.add(
            Feed(
                name="Real-server fixture feed",
                url="https://source.example.com/browser-rss",
                enabled=False,
                handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    initialize()
    uvicorn.run(harness, host="127.0.0.1", port=arguments.port, access_log=False)

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request, status
from fastapi.responses import RedirectResponse

from app.models.oidc import OIDCProvider
from app.services.oidc_client import OIDCClaims
from app.services.oidc_transaction import clear_oidc_transaction_cookie


def accepts_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "").lower()


def oidc_identity_assurance(claims: OIDCClaims) -> tuple[str | None, list[str]]:
    acr_claim = claims.claims.get("acr")
    acr = acr_claim.strip()[:255] if isinstance(acr_claim, str) else None
    amr_claim = claims.claims.get("amr")
    amr = (
        list(
            dict.fromkeys(
                value.strip().lower()
                for value in amr_claim
                if isinstance(value, str) and value.strip()
            )
        )[:32]
        if isinstance(amr_claim, list)
        else []
    )
    return acr or None, amr


def identity_claim_diagnostics(claims: OIDCClaims | None) -> dict[str, object]:
    if claims is None:
        return {"claims_available": False}

    email = claims.claims.get("email")
    email_verified = claims.claims.get("email_verified")
    return {
        "claims_available": True,
        "email_claim_present": "email" in claims.claims,
        "email_value_present": isinstance(email, str) and bool(email.strip()),
        "email_claim_type": type(email).__name__ if email is not None else None,
        "email_verified_claim_present": "email_verified" in claims.claims,
        "email_verified": email_verified if isinstance(email_verified, bool) else None,
        "email_verified_claim_type": type(email_verified).__name__
        if email_verified is not None
        else None,
    }


def callback_redirect(
    provider: OIDCProvider | None, path: str, query: dict[str, str]
) -> RedirectResponse:
    base_url = provider.public_base_url.rstrip("/") if provider else ""
    target = f"{base_url}{path}"
    if query:
        target = f"{target}?{urlencode(query)}"
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    clear_oidc_transaction_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response

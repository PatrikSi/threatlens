from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oidc.core import CodeIDToken, UserInfo
from joserfc import jwt as jose_jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet

from app.core.config import get_settings
from app.models.oidc import OIDCProvider
from app.services.oidc_config import OIDCConfigurationError, oidc_callback_url, validate_oidc_endpoint_url
from app.services.safe_fetch import build_safe_http_client, safe_stream_with_redirects
from app.services.secret_storage import decrypt_text
from app.services.url_utils import ensure_runtime_fetchable_url

SUPPORTED_ID_TOKEN_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"})


class OIDCProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str | None
    token_endpoint_auth_methods_supported: tuple[str, ...]
    id_token_signing_alg_values_supported: tuple[str, ...]


@dataclass(frozen=True)
class OIDCClaims:
    issuer: str
    subject: str
    claims: dict[str, Any]


_metadata_cache: dict[str, tuple[float, str, OIDCMetadata]] = {}
_metadata_cache_lock = threading.Lock()


def load_oidc_metadata(provider: OIDCProvider, *, force: bool = False) -> OIDCMetadata:
    settings = get_settings()
    cache_key = str(provider.id)
    config_marker = f"{provider.updated_at.isoformat() if provider.updated_at else ''}:{provider.issuer_url}"
    now = time.monotonic()
    if not force:
        with _metadata_cache_lock:
            cached = _metadata_cache.get(cache_key)
            if cached and cached[0] > now and cached[1] == config_marker:
                return cached[2]

    discovery_url = f"{provider.issuer_url.rstrip('/')}/.well-known/openid-configuration"
    raw_metadata = _fetch_json("GET", discovery_url, allow_redirects=True)
    metadata = _parse_metadata(provider, raw_metadata)
    with _metadata_cache_lock:
        _metadata_cache[cache_key] = (now + settings.oidc_metadata_cache_seconds, config_marker, metadata)
    return metadata


def build_oidc_authorization_url(
    provider: OIDCProvider,
    metadata: OIDCMetadata,
    *,
    state: str,
    nonce: str,
    code_verifier: str,
) -> str:
    with OAuth2Client(
        client_id=provider.client_id,
        scope=" ".join(provider.scopes),
        redirect_uri=oidc_callback_url(provider.public_base_url),
        code_challenge_method="S256",
    ) as client:
        authorization_url, returned_state = client.create_authorization_url(
            metadata.authorization_endpoint,
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            response_type="code",
        )
    if returned_state != state:
        raise OIDCProtocolError("OIDC client returned an unexpected authorization state")
    return authorization_url


def exchange_oidc_code(
    provider: OIDCProvider,
    metadata: OIDCMetadata,
    *,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    _ensure_auth_method_supported(provider, metadata)
    form: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": oidc_callback_url(provider.public_base_url),
        "code_verifier": code_verifier,
    }
    auth: httpx.Auth | None = None
    client_secret = decrypt_text(provider.client_secret_encrypted)
    if provider.client_auth_method == "client_secret_basic":
        if not client_secret:
            raise OIDCConfigurationError("OIDC client secret is required for client_secret_basic")
        auth = httpx.BasicAuth(provider.client_id, client_secret)
    elif provider.client_auth_method == "client_secret_post":
        if not client_secret:
            raise OIDCConfigurationError("OIDC client secret is required for client_secret_post")
        form.update({"client_id": provider.client_id, "client_secret": client_secret})
    else:
        form["client_id"] = provider.client_id

    token = _fetch_json("POST", metadata.token_endpoint, data=form, auth=auth)
    if "error" in token:
        error_code = str(token.get("error") or "token_exchange_failed")[:128]
        raise OIDCProtocolError(f"OIDC token endpoint rejected the authorization code ({error_code})")
    if not isinstance(token.get("id_token"), str) or not token["id_token"]:
        raise OIDCProtocolError("OIDC token response did not include an ID token")
    if not isinstance(token.get("access_token"), str) or not token["access_token"]:
        raise OIDCProtocolError("OIDC token response did not include an access token")
    return token


def validate_oidc_token_claims(
    provider: OIDCProvider,
    metadata: OIDCMetadata,
    token: dict[str, Any],
    *,
    nonce: str,
) -> OIDCClaims:
    key_set = _load_jwks(metadata)
    algorithms = _allowed_signing_algorithms(metadata)
    try:
        parsed_token = jose_jwt.decode(token["id_token"], key_set, algorithms=algorithms)
    except InvalidKeyIdError:
        key_set = _load_jwks(metadata)
        parsed_token = jose_jwt.decode(token["id_token"], key_set, algorithms=algorithms)
    except Exception as exc:
        raise OIDCProtocolError("OIDC ID token signature validation failed") from exc

    claims = CodeIDToken(
        parsed_token.claims,
        parsed_token.header,
        {"iss": {"values": [metadata.issuer]}},
        {
            "nonce": nonce,
            "client_id": provider.client_id,
            "access_token": token["access_token"],
        },
    )
    try:
        claims.validate(leeway=60)
    except Exception as exc:
        raise OIDCProtocolError("OIDC ID token claims validation failed") from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 512:
        raise OIDCProtocolError("OIDC ID token subject is missing or invalid")

    combined_claims = dict(UserInfo(claims))
    if metadata.userinfo_endpoint:
        userinfo = _fetch_json(
            "GET",
            metadata.userinfo_endpoint,
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        userinfo_subject = userinfo.get("sub")
        if userinfo_subject != subject:
            raise OIDCProtocolError("OIDC UserInfo subject does not match the ID token")
        protected_claims = {"iss", "aud", "exp", "iat", "nbf", "nonce", "azp", "auth_time", "sub"}
        combined_claims.update({key: value for key, value in userinfo.items() if key not in protected_claims})

    return OIDCClaims(issuer=metadata.issuer, subject=subject, claims=combined_claims)


def test_oidc_provider(provider: OIDCProvider) -> tuple[OIDCMetadata, int]:
    metadata = load_oidc_metadata(provider, force=True)
    key_set = _load_jwks(metadata)
    return metadata, len(key_set.as_dict().get("keys", []))


def _parse_metadata(provider: OIDCProvider, raw: dict[str, Any]) -> OIDCMetadata:
    issuer = raw.get("issuer")
    if issuer != provider.issuer_url:
        raise OIDCProtocolError("OIDC discovery issuer does not exactly match the configured issuer")

    required_endpoints: dict[str, str] = {}
    for field_name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = raw.get(field_name)
        if not isinstance(value, str) or not value:
            raise OIDCProtocolError(f"OIDC discovery metadata is missing {field_name}")
        validate_oidc_endpoint_url(value, field_name=field_name.replace("_", " ").title())
        required_endpoints[field_name] = value

    userinfo_endpoint = raw.get("userinfo_endpoint")
    if userinfo_endpoint is not None:
        if not isinstance(userinfo_endpoint, str) or not userinfo_endpoint:
            raise OIDCProtocolError("OIDC discovery userinfo_endpoint is invalid")
        validate_oidc_endpoint_url(userinfo_endpoint, field_name="UserInfo endpoint")

    auth_methods = raw.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
    if not isinstance(auth_methods, list) or not all(isinstance(value, str) for value in auth_methods):
        raise OIDCProtocolError("OIDC discovery token authentication methods are invalid")
    signing_algorithms = raw.get("id_token_signing_alg_values_supported", ["RS256"])
    if not isinstance(signing_algorithms, list) or not all(isinstance(value, str) for value in signing_algorithms):
        raise OIDCProtocolError("OIDC discovery signing algorithms are invalid")

    return OIDCMetadata(
        issuer=issuer,
        authorization_endpoint=required_endpoints["authorization_endpoint"],
        token_endpoint=required_endpoints["token_endpoint"],
        jwks_uri=required_endpoints["jwks_uri"],
        userinfo_endpoint=userinfo_endpoint,
        token_endpoint_auth_methods_supported=tuple(auth_methods),
        id_token_signing_alg_values_supported=tuple(signing_algorithms),
    )


def _allowed_signing_algorithms(metadata: OIDCMetadata) -> tuple[str, ...]:
    allowed = tuple(
        algorithm
        for algorithm in metadata.id_token_signing_alg_values_supported
        if algorithm in SUPPORTED_ID_TOKEN_ALGORITHMS
    )
    if not allowed:
        raise OIDCProtocolError("OIDC provider does not advertise a supported asymmetric ID token signing algorithm")
    return allowed


def _ensure_auth_method_supported(provider: OIDCProvider, metadata: OIDCMetadata) -> None:
    if provider.client_auth_method not in metadata.token_endpoint_auth_methods_supported:
        raise OIDCConfigurationError(
            f"OIDC provider does not advertise support for {provider.client_auth_method}"
        )


def _load_jwks(metadata: OIDCMetadata) -> KeySet:
    raw = _fetch_json("GET", metadata.jwks_uri)
    keys = raw.get("keys")
    if not isinstance(keys, list) or not keys:
        raise OIDCProtocolError("OIDC JWKS response does not contain signing keys")
    try:
        return KeySet.import_key_set(raw)
    except Exception as exc:
        raise OIDCProtocolError("OIDC JWKS response is invalid") from exc


def _fetch_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    auth: httpx.Auth | None = None,
    allow_redirects: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    timeout = httpx.Timeout(
        connect=settings.oidc_connect_timeout_seconds,
        read=settings.oidc_read_timeout_seconds,
        write=settings.oidc_read_timeout_seconds,
        pool=settings.oidc_connect_timeout_seconds,
    )
    request_headers = {"Accept": "application/json", "User-Agent": settings.fetch_user_agent, **(headers or {})}
    try:
        with build_safe_http_client(
            timeout=timeout,
            headers=request_headers,
            allow_private_network=settings.allow_private_network_oidc,
        ) as client:
            if allow_redirects and method.upper() == "GET":
                response = safe_stream_with_redirects(
                    client,
                    "GET",
                    url,
                    allow_private_network=settings.allow_private_network_oidc,
                    max_redirects=min(settings.outbound_max_redirects, 3),
                )
            else:
                ensure_runtime_fetchable_url(url, allow_private_network=settings.allow_private_network_oidc)
                request = client.build_request(method.upper(), url, data=data)
                response = client.send(request, stream=True, auth=auth, follow_redirects=False)
            try:
                if response.is_redirect:
                    raise OIDCProtocolError("OIDC endpoint returned an unexpected redirect")
                if response.status_code < 200 or response.status_code >= 300:
                    raise OIDCProtocolError(f"OIDC endpoint returned HTTP {response.status_code}")
                payload = _read_limited_body(response)
            finally:
                response.close()
    except (OIDCProtocolError, OIDCConfigurationError):
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise OIDCProtocolError("OIDC endpoint request failed") from exc

    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OIDCProtocolError("OIDC endpoint returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise OIDCProtocolError("OIDC endpoint returned an unexpected JSON payload")
    return parsed


def _read_limited_body(response: httpx.Response) -> bytes:
    max_bytes = get_settings().oidc_max_response_bytes
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise OIDCProtocolError("OIDC endpoint response exceeded the configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)

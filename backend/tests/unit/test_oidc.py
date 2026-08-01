import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from joserfc.jwk import KeySet
from joserfc.errors import InvalidKeyIdError

from app.core.config import get_settings
from app.models.oidc import OIDCProvider
from app.schemas.oidc import OIDCProviderUpdateRequest
from app.services.oidc_client import (
    OIDCMetadata,
    OIDCProtocolError,
    build_oidc_authorization_url,
    validate_oidc_token_claims,
)
from app.services import oidc_client, oidc_config
from app.services.oidc_config import OIDCConfigurationError, oidc_callback_url, validate_oidc_provider_urls
from app.services.oidc_identity import resolve_oidc_role
from app.services.oidc_transaction import (
    decode_oidc_transaction,
    encode_oidc_transaction,
    new_oidc_transaction,
)


def _provider(**overrides) -> OIDCProvider:
    values = {
        "name": "Company SSO",
        "enabled": True,
        "issuer_url": "https://idp.example.com",
        "client_id": "threatlens",
        "client_auth_method": "client_secret_basic",
        "public_base_url": "https://threatlens.example.com",
        "scopes": ["openid", "profile", "email", "groups"],
        "role_claim": "groups",
        "role_mappings_json": [],
        "default_role": "viewer",
    }
    values.update(overrides)
    return OIDCProvider(**values)


def _request_with_cookie(cookie_name: str, cookie_value: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/oidc/callback",
        "headers": [(b"cookie", f"{cookie_name}={cookie_value}".encode("ascii"))],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
    }
    return Request(scope)


def test_oidc_provider_schema_requires_openid_and_unique_mapping_values():
    with pytest.raises(ValueError, match="must include openid"):
        OIDCProviderUpdateRequest(scopes=["profile"])

    with pytest.raises(ValueError, match="must be unique"):
        OIDCProviderUpdateRequest(
            role_mappings=[
                {"claim_value": "soc-admins", "role": "admin"},
                {"claim_value": "soc-admins", "role": "viewer"},
            ]
        )

    request = OIDCProviderUpdateRequest(client_secret="  opaque secret  ")
    assert request.client_secret == "  opaque secret  "


def test_oidc_role_mapping_supports_nested_claims_and_uses_highest_role():
    provider = _provider(
        role_claim="realm_access.roles",
        role_mappings_json=[
            {"claim_value": "readers", "role": "viewer"},
            {"claim_value": "responders", "role": "analyst"},
            {"claim_value": "soc-admins", "role": "admin"},
        ],
    )

    assert resolve_oidc_role(provider, {"realm_access": {"roles": ["readers", "responders"]}}) == "analyst"
    assert resolve_oidc_role(provider, {"realm_access": {"roles": ["responders", "soc-admins"]}}) == "admin"
    assert resolve_oidc_role(provider, {"realm_access": {"roles": ["unmapped"]}}) == "viewer"


def test_oidc_urls_require_https_by_default(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_private_network_oidc", False)
    monkeypatch.setattr(settings, "allow_insecure_http_oidc", False)

    with pytest.raises(OIDCConfigurationError, match="must use HTTPS"):
        validate_oidc_provider_urls(
            issuer_url="http://idp.example.com",
            public_base_url="https://threatlens.example.com",
        )


def test_oidc_urls_allow_http_only_with_explicit_transport_and_network_opt_ins(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_insecure_http_oidc", True)
    monkeypatch.setattr(settings, "allow_private_network_oidc", False)
    monkeypatch.setattr(
        oidc_config,
        "is_fetchable_url",
        lambda _url, *, allow_private_network: allow_private_network,
    )

    with pytest.raises(OIDCConfigurationError, match="ALLOW_PRIVATE_NETWORK_OIDC"):
        validate_oidc_provider_urls(
            issuer_url="http://idp.internal",
            public_base_url="http://threatlens.internal",
        )

    monkeypatch.setattr(settings, "allow_private_network_oidc", True)
    validate_oidc_provider_urls(
        issuer_url="http://idp.internal",
        public_base_url="http://threatlens.internal",
    )


def test_oidc_urls_allow_public_http_with_explicit_insecure_transport_opt_in(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_insecure_http_oidc", True)
    monkeypatch.setattr(settings, "allow_private_network_oidc", False)
    monkeypatch.setattr(oidc_config, "is_fetchable_url", lambda *_args, **_kwargs: True)

    validate_oidc_provider_urls(
        issuer_url="http://idp.example.com",
        public_base_url="http://threatlens.example.com",
    )


def test_private_http_oidc_remains_backward_compatible(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_insecure_http_oidc", False)
    monkeypatch.setattr(settings, "allow_private_network_oidc", True)
    monkeypatch.setattr(
        oidc_config,
        "is_fetchable_url",
        lambda _url, *, allow_private_network: allow_private_network,
    )

    validate_oidc_provider_urls(
        issuer_url="http://idp.internal",
        public_base_url="http://threatlens.internal",
    )


def test_oidc_callback_path_is_configurable(monkeypatch):
    monkeypatch.setattr(get_settings(), "oidc_callback_path", "/v1/auth/oidc/callback")
    assert oidc_callback_url("https://threatlens.example.com") == "https://threatlens.example.com/v1/auth/oidc/callback"


def test_oidc_transaction_rejects_state_mismatch():
    transaction = new_oidc_transaction(provider_id="provider-1", mode="login")
    encoded = encode_oidc_transaction(transaction)
    settings = get_settings()
    request = _request_with_cookie(settings.oidc_transaction_cookie_name, encoded)

    assert decode_oidc_transaction(request, "different-state") is None
    assert decode_oidc_transaction(request, transaction.state) == transaction


def test_authorization_url_contains_pkce_nonce_and_exact_callback():
    provider = _provider()
    metadata = OIDCMetadata(
        issuer=provider.issuer_url,
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint="https://idp.example.com/userinfo",
        token_endpoint_auth_methods_supported=("client_secret_basic",),
        id_token_signing_alg_values_supported=("RS256",),
    )

    authorization_url = build_oidc_authorization_url(
        provider,
        metadata,
        state="state-value",
        nonce="nonce-value",
        code_verifier="a" * 64,
    )
    query = parse_qs(urlsplit(authorization_url).query)

    assert query["state"] == ["state-value"]
    assert query["nonce"] == ["nonce-value"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://threatlens.example.com/api/v1/auth/oidc/callback"]


def test_id_token_validation_checks_signature_audience_issuer_and_nonce(monkeypatch):
    provider = _provider()
    metadata = OIDCMetadata(
        issuer=provider.issuer_url,
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint=None,
        token_endpoint_auth_methods_supported=("client_secret_basic",),
        id_token_signing_alg_values_supported=("RS256",),
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    key_set = KeySet.import_key_set({"keys": [public_jwk]})
    monkeypatch.setattr("app.services.oidc_client._load_jwks", lambda _metadata: key_set)
    now = datetime.now(timezone.utc)

    def token_for(nonce: str):
        id_token = jwt.encode(
            {
                "iss": provider.issuer_url,
                "sub": "subject-1",
                "aud": provider.client_id,
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "nonce": nonce,
                "email": "analyst@example.com",
                "email_verified": True,
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )
        return {"id_token": id_token, "access_token": "access-token"}

    claims = validate_oidc_token_claims(provider, metadata, token_for("expected-nonce"), nonce="expected-nonce")
    assert claims.subject == "subject-1"
    assert claims.claims["email_verified"] is True

    with pytest.raises(OIDCProtocolError, match="claims validation failed"):
        validate_oidc_token_claims(provider, metadata, token_for("wrong-nonce"), nonce="expected-nonce")


def test_id_token_validation_wraps_failure_after_jwks_refresh(monkeypatch):
    provider = _provider()
    metadata = OIDCMetadata(
        issuer=provider.issuer_url,
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint=None,
        token_endpoint_auth_methods_supported=("client_secret_basic",),
        id_token_signing_alg_values_supported=("RS256",),
    )
    decode_attempts = 0

    def fail_decode(*_args, **_kwargs):
        nonlocal decode_attempts
        decode_attempts += 1
        if decode_attempts == 1:
            raise InvalidKeyIdError()
        raise ValueError("rotated key is still unavailable")

    monkeypatch.setattr("app.services.oidc_client._load_jwks", lambda _metadata: object())
    monkeypatch.setattr("app.services.oidc_client.jose_jwt.decode", fail_decode)

    with pytest.raises(OIDCProtocolError, match="signature validation failed"):
        validate_oidc_token_claims(
            provider,
            metadata,
            {"id_token": "id-token", "access_token": "access-token"},
            nonce="nonce",
        )

    assert decode_attempts == 2


def test_provider_connection_test_rejects_unsupported_client_auth_method(monkeypatch):
    provider = _provider(client_auth_method="client_secret_post")
    metadata = OIDCMetadata(
        issuer=provider.issuer_url,
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint=None,
        token_endpoint_auth_methods_supported=("client_secret_basic",),
        id_token_signing_alg_values_supported=("RS256",),
    )
    monkeypatch.setattr(oidc_client, "load_oidc_metadata", lambda _provider, *, force: metadata)

    with pytest.raises(OIDCConfigurationError, match="client_secret_post"):
        oidc_client.test_oidc_provider(provider)


def test_oidc_json_fetch_closes_streamed_httpx_response(monkeypatch):
    response = oidc_client.httpx.Response(
        200,
        content=b'{"issuer":"https://idp.example.com"}',
        request=oidc_client.httpx.Request("GET", "https://idp.example.com/metadata"),
    )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def build_request(self, method, url, data=None):
            return oidc_client.httpx.Request(method, url, data=data)

        def send(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(oidc_client, "build_safe_http_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(oidc_client, "ensure_runtime_fetchable_url", lambda *_args, **_kwargs: None)

    assert oidc_client._fetch_json("GET", "https://idp.example.com/metadata") == {
        "issuer": "https://idp.example.com"
    }
    assert response.is_closed is True

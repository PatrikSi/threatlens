import json
import socket
import uuid
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
from app.services.secret_storage import encrypt_text
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
    assert OIDCProviderUpdateRequest().require_verified_email is True

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

    userinfo_metadata = OIDCMetadata(
        issuer=metadata.issuer,
        authorization_endpoint=metadata.authorization_endpoint,
        token_endpoint=metadata.token_endpoint,
        jwks_uri=metadata.jwks_uri,
        userinfo_endpoint="https://idp.example.com/userinfo",
        token_endpoint_auth_methods_supported=metadata.token_endpoint_auth_methods_supported,
        id_token_signing_alg_values_supported=metadata.id_token_signing_alg_values_supported,
    )
    monkeypatch.setattr(
        oidc_client,
        "_fetch_json",
        lambda *_args, **_kwargs: {"sub": "different-subject", "email": "attacker@example.com"},
    )
    with pytest.raises(OIDCProtocolError, match="UserInfo subject does not match"):
        validate_oidc_token_claims(
            provider,
            userinfo_metadata,
            token_for("expected-nonce"),
            nonce="expected-nonce",
        )


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


def test_oidc_metadata_load_caches_discovery_and_supports_forced_refresh(monkeypatch):
    provider = _provider()
    provider.id = uuid.uuid4()
    discovery_payload = {
        "issuer": provider.issuer_url,
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
        "jwks_uri": "https://idp.example.com/jwks",
        "userinfo_endpoint": "https://idp.example.com/userinfo",
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }
    fetches: list[tuple[str, str]] = []

    def fetch_json(method, url, **_kwargs):
        fetches.append((method, url))
        return discovery_payload

    monkeypatch.setattr(oidc_client, "_fetch_json", fetch_json)
    monkeypatch.setattr(oidc_client, "validate_oidc_endpoint_url", lambda *_args, **_kwargs: None)
    oidc_client._metadata_cache.clear()
    try:
        first = oidc_client.load_oidc_metadata(provider)
        second = oidc_client.load_oidc_metadata(provider)
        refreshed = oidc_client.load_oidc_metadata(provider, force=True)
    finally:
        oidc_client._metadata_cache.clear()

    assert first == second == refreshed
    assert first.userinfo_endpoint == "https://idp.example.com/userinfo"
    assert fetches == [
        ("GET", "https://idp.example.com/.well-known/openid-configuration"),
        ("GET", "https://idp.example.com/.well-known/openid-configuration"),
    ]


def test_oidc_code_exchange_preserves_secret_and_pkce_fields(monkeypatch):
    provider = _provider(
        client_auth_method="client_secret_post",
        client_secret_encrypted=encrypt_text("  opaque secret  "),
    )
    metadata = OIDCMetadata(
        issuer=provider.issuer_url,
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint=None,
        token_endpoint_auth_methods_supported=("client_secret_post",),
        id_token_signing_alg_values_supported=("RS256",),
    )
    captured: dict[str, object] = {}

    def fetch_json(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return {"id_token": "id-token", "access_token": "access-token"}

    monkeypatch.setattr(oidc_client, "_fetch_json", fetch_json)

    token = oidc_client.exchange_oidc_code(
        provider,
        metadata,
        code="authorization-code",
        code_verifier="code-verifier",
    )

    assert token == {"id_token": "id-token", "access_token": "access-token"}
    assert captured["method"] == "POST"
    assert captured["url"] == metadata.token_endpoint
    assert captured["auth"] is None
    assert captured["data"] == {
        "grant_type": "authorization_code",
        "code": "authorization-code",
        "redirect_uri": "https://threatlens.example.com/api/v1/auth/oidc/callback",
        "code_verifier": "code-verifier",
        "client_id": "threatlens",
        "client_secret": "  opaque secret  ",
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"error": "invalid_grant"}, "invalid_grant"),
        ({"access_token": "access-token"}, "did not include an ID token"),
        ({"id_token": "id-token"}, "did not include an access token"),
    ],
)
def test_oidc_code_exchange_rejects_incomplete_token_responses(monkeypatch, payload, message):
    provider = _provider(client_auth_method="none")
    metadata = OIDCMetadata(
        issuer=provider.issuer_url,
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        jwks_uri="https://idp.example.com/jwks",
        userinfo_endpoint=None,
        token_endpoint_auth_methods_supported=("none",),
        id_token_signing_alg_values_supported=("RS256",),
    )
    monkeypatch.setattr(oidc_client, "_fetch_json", lambda *_args, **_kwargs: payload)

    with pytest.raises(OIDCProtocolError, match=message):
        oidc_client.exchange_oidc_code(
            provider,
            metadata,
            code="authorization-code",
            code_verifier="code-verifier",
        )


@pytest.mark.parametrize(
    ("content", "status_code", "message"),
    [
        (b"not-json", 200, "invalid JSON"),
        (b'{}', 502, "HTTP 502"),
        (b'{}', 302, "unexpected redirect"),
    ],
)
def test_oidc_json_fetch_rejects_invalid_responses_and_closes_stream(
    monkeypatch,
    content,
    status_code,
    message,
):
    response = oidc_client.httpx.Response(
        status_code,
        content=content,
        headers={"Location": "https://other.example.com"} if status_code == 302 else None,
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

    with pytest.raises(OIDCProtocolError, match=message):
        oidc_client._fetch_json("GET", "https://idp.example.com/metadata")

    assert response.is_closed is True


@pytest.mark.parametrize(
    ("request_error", "message"),
    [
        (oidc_client.httpx.ConnectError("connection failed"), "connection failed"),
        (oidc_client.httpx.ConnectTimeout("connect timeout"), "connection timed out"),
        (oidc_client.httpx.ReadTimeout("read timeout"), "response timed out"),
        (oidc_client.httpx.PoolTimeout("pool timeout"), "request timed out"),
    ],
)
def test_oidc_json_fetch_classifies_network_failures(monkeypatch, request_error, message):
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def build_request(self, method, url, data=None):
            return oidc_client.httpx.Request(method, url, data=data)

        def send(self, *_args, **_kwargs):
            raise request_error

    monkeypatch.setattr(oidc_client, "build_safe_http_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(oidc_client, "ensure_runtime_fetchable_url", lambda *_args, **_kwargs: None)

    with pytest.raises(OIDCProtocolError, match=message):
        oidc_client._fetch_json("GET", "https://idp.example.com/metadata")


def test_oidc_json_fetch_identifies_dns_resolution_failures(monkeypatch):
    request_error = oidc_client.httpx.ConnectError("connection failed")
    request_error.__cause__ = socket.gaierror(-3, "Temporary failure in name resolution")

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def build_request(self, method, url, data=None):
            return oidc_client.httpx.Request(method, url, data=data)

        def send(self, *_args, **_kwargs):
            raise request_error

    monkeypatch.setattr(oidc_client, "build_safe_http_client", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(oidc_client, "ensure_runtime_fetchable_url", lambda *_args, **_kwargs: None)

    with pytest.raises(OIDCProtocolError, match="hostname could not be resolved"):
        oidc_client._fetch_json("GET", "https://idp.example.com/metadata")


def test_oidc_runtime_url_check_distinguishes_dns_and_network_policy(monkeypatch):
    monkeypatch.setattr(oidc_client, "is_fetchable_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        oidc_client,
        "ensure_runtime_fetchable_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("not fetchable")),
    )
    monkeypatch.setattr(oidc_client, "resolve_hostname_ips", lambda _hostname: set())

    with pytest.raises(OIDCProtocolError, match="hostname could not be resolved"):
        oidc_client._ensure_oidc_runtime_fetchable_url(
            "http://authentik.patriksi.local/discovery",
            allow_private_network=True,
        )

    monkeypatch.setattr(oidc_client, "is_fetchable_url", lambda *_args, **_kwargs: False)
    with pytest.raises(OIDCProtocolError, match="blocked by outbound network policy"):
        oidc_client._ensure_oidc_runtime_fetchable_url(
            "http://authentik.patriksi.local/discovery",
            allow_private_network=False,
        )


def test_oidc_failure_reason_bounds_expected_errors_and_hides_unexpected_values():
    expected = OIDCProtocolError(f"provider failure\n{'x' * 600}")

    assert oidc_client.oidc_failure_reason(expected).startswith("provider failure ")
    assert len(oidc_client.oidc_failure_reason(expected)) == 512
    assert oidc_client.oidc_failure_reason(ValueError("sensitive value")) == "OIDC validation failed"


def test_oidc_json_fetch_enforces_response_size_limit(monkeypatch):
    response = oidc_client.httpx.Response(
        200,
        content=b'{"value":"larger than limit"}',
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
    monkeypatch.setattr(get_settings(), "oidc_max_response_bytes", 8)

    with pytest.raises(OIDCProtocolError, match="size limit"):
        oidc_client._fetch_json("GET", "https://idp.example.com/metadata")

    assert response.is_closed is True


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

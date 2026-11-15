"""OIDC's transport opt-ins must cover initial requests and every redirect."""

import ipaddress
import json

import httpx
import pytest

from app.core.config import get_settings
from app.services import oidc_client, url_utils
from app.services.oidc_config import OIDCConfigurationError


DISCOVERY = "https://idp.example.com/.well-known/openid-configuration"


def _transport(monkeypatch, redirects):
    """Exercise the real redirect loop with deterministic DNS and no sockets."""
    requests, responses = [], []

    def respond(request):
        url = str(request.url)
        requests.append(url)
        if url in redirects:
            response = httpx.Response(
                302,
                headers={"Location": redirects[url]},
                stream=httpx.ByteStream(b"redirect"),
            )
        else:
            response = httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=httpx.ByteStream(json.dumps({"ok": True}).encode()),
            )
        responses.append(response)
        return response

    monkeypatch.setattr(
        oidc_client,
        "build_safe_http_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(respond),
            timeout=kwargs["timeout"],
            headers=kwargs["headers"],
        ),
    )
    monkeypatch.setattr(
        url_utils,
        "resolve_hostname_ips",
        lambda hostname: {
            ipaddress.ip_address(
                "10.0.0.12" if hostname.endswith(".internal") else "93.184.216.34"
            )
        },
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_insecure_http_oidc", False)
    monkeypatch.setattr(settings, "allow_private_network_oidc", False)
    return settings, requests, responses


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_oidc_initial_request_rechecks_current_https_requirement(monkeypatch, method):
    _settings, requests, _responses = _transport(monkeypatch, {})

    with pytest.raises(OIDCConfigurationError, match="must use HTTPS"):
        oidc_client._fetch_json(method, "http://idp.example.com/endpoint")

    assert requests == []


@pytest.mark.parametrize("private_network_enabled", [False, True])
@pytest.mark.parametrize("intermediate", [None, "https://metadata.example.com/discovery"])
def test_oidc_discovery_blocks_public_http_downgrade_before_request(
    monkeypatch, private_network_enabled, intermediate
):
    plaintext = "http://metadata.example.com/discovery"
    redirects = {DISCOVERY: intermediate or plaintext}
    if intermediate:
        redirects[intermediate] = plaintext
    settings, requests, responses = _transport(monkeypatch, redirects)
    monkeypatch.setattr(settings, "allow_private_network_oidc", private_network_enabled)

    with pytest.raises(OIDCConfigurationError, match="must use HTTPS"):
        oidc_client._fetch_json("GET", DISCOVERY, allow_redirects=True)

    assert requests == [DISCOVERY] + ([intermediate] if intermediate else [])
    assert responses and all(response.is_closed for response in responses)


@pytest.mark.parametrize("location", [
    "https://metadata.example.com/discovery",
    "//metadata.example.com/discovery",
    "/redirected-discovery",
])
def test_oidc_discovery_keeps_safe_https_redirects(monkeypatch, location):
    _settings, requests, responses = _transport(monkeypatch, {DISCOVERY: location})

    assert oidc_client._fetch_json("GET", DISCOVERY, allow_redirects=True) == {"ok": True}

    assert len(requests) == 2
    assert all(request.startswith("https://") for request in requests)
    assert all(response.is_closed for response in responses)


@pytest.mark.parametrize("redirect", [False, True])
def test_oidc_public_http_requires_the_explicit_transport_opt_in(monkeypatch, redirect):
    plaintext = "http://metadata.example.com/discovery"
    settings, requests, responses = _transport(
        monkeypatch, {DISCOVERY: plaintext} if redirect else {}
    )
    monkeypatch.setattr(settings, "allow_insecure_http_oidc", True)

    assert oidc_client._fetch_json(
        "GET", DISCOVERY if redirect else plaintext, allow_redirects=True
    ) == {"ok": True}

    assert requests == ([DISCOVERY] if redirect else []) + [plaintext]
    assert all(response.is_closed for response in responses)


@pytest.mark.parametrize("redirect", [False, True])
def test_oidc_private_http_keeps_the_existing_network_opt_in(monkeypatch, redirect):
    plaintext = "http://idp.internal/discovery"
    settings, requests, responses = _transport(
        monkeypatch, {DISCOVERY: plaintext} if redirect else {}
    )
    monkeypatch.setattr(settings, "allow_private_network_oidc", True)

    assert oidc_client._fetch_json(
        "GET", DISCOVERY if redirect else plaintext, allow_redirects=True
    ) == {"ok": True}

    assert settings.allow_insecure_http_oidc is False
    assert requests == ([DISCOVERY] if redirect else []) + [plaintext]
    assert all(response.is_closed for response in responses)


def test_oidc_discovery_rejects_redirect_embedded_credentials(monkeypatch):
    destination = "https://redirect-user:synthetic-password@metadata.example.com/discovery"
    _settings, requests, responses = _transport(monkeypatch, {DISCOVERY: destination})

    with pytest.raises(OIDCConfigurationError, match="embedded credentials"):
        oidc_client._fetch_json("GET", DISCOVERY, allow_redirects=True)

    assert requests == [DISCOVERY]
    assert all(response.is_closed for response in responses)


def test_oidc_token_post_still_rejects_redirects_without_following(monkeypatch):
    endpoint = "https://idp.example.com/token"
    _settings, requests, responses = _transport(
        monkeypatch, {endpoint: "http://metadata.example.com/token"}
    )

    with pytest.raises(oidc_client.OIDCProtocolError, match="unexpected redirect"):
        oidc_client._fetch_json("POST", endpoint, data={"code": "synthetic-code"})

    assert requests == [endpoint]
    assert all(response.is_closed for response in responses)

"""Reject unusable registrations before codes, secrets or database rows exist."""

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.mcp_oauth import OAuthAuthorization, OAuthClientCreate
from app.schemas.publication_consumers import ConsumerCreate


@pytest.mark.parametrize("name", ["   ", "bad\x00name", "bad\ud800name"])
def test_registration_names_reject_blank_and_unstorable_text(name):
    with pytest.raises(ValidationError):
        OAuthClientCreate(name=name, redirect_uris=["https://client.example/cb"])
    with pytest.raises(ValidationError):
        ConsumerCreate(name=name, idempotency_key=uuid.uuid4())


@pytest.mark.parametrize(
    "uri",
    [
        "https://client.example:invalid/cb",
        "https://client.example:65536/cb",
        "https://client.example\\attacker.example/cb",
        "https://client.example/\ud800",
        "https://user:password@client.example/cb",
        "http://remote.example/cb",
        "https://client.example/cb?next=elsewhere",
        "https://client.example/cb#fragment",
        "https://client.example/cb\n",
    ],
)
def test_client_redirects_reject_ambiguous_or_unusable_addresses(uri):
    with pytest.raises(ValidationError):
        OAuthClientCreate(name="Client", redirect_uris=[uri])


def test_client_preserves_exact_valid_callback_and_trims_display_name():
    callback = "http://[::1]:8123/callback"
    client = OAuthClientCreate(name="  Desktop client  ", redirect_uris=[callback])
    assert client.name == "Desktop client"
    assert client.redirect_uris == [callback]


def test_authorization_rejects_unstorable_state_before_redirect_encoding():
    with pytest.raises(ValidationError):
        OAuthAuthorization(
            client_id=uuid.uuid4(),
            redirect_uri="https://client.example/callback",
            resource="https://threatlens.example/api/v1/mcp",
            code_challenge_method="S256",
            code_challenge="x" * 43,
            state="state-value-16chars-\ud800",
        )

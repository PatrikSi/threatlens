import pytest
from pydantic import ValidationError

from app.core.outbound_headers import BLOCKED_REQUEST_HEADERS
from app.schemas.webhook_automation import WebhookCredentialWrite


@pytest.mark.parametrize(
    "header",
    sorted(
        BLOCKED_REQUEST_HEADERS
        | {
            "content-type",
            "cookie",
            "location",
            "authorization",
            "x-threatlens-signature",
        }
    ),
)
def test_transport_and_signature_headers_cannot_be_credential_headers(header):
    with pytest.raises(ValidationError, match="reserved"):
        WebhookCredentialWrite(
            name="SIEM",
            auth_type="header",
            header_name=header.upper(),
            auth_secret="test-key",
        )


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "line\rbreak",
        "line\nbreak",
        "tab\tkey",
        "nul\x00key",
        "delete\x7fkey",
        "nonascii-é",
        " leading",
        "trailing ",
    ],
)
def test_authentication_header_values_fail_before_storage_or_delivery(secret):
    with pytest.raises(ValidationError, match="printable ASCII|cannot be stored"):
        WebhookCredentialWrite(name="SIEM", auth_type="bearer", auth_secret=secret)


def test_printable_custom_keys_and_unicode_hmac_secrets_are_supported():
    profile = WebhookCredentialWrite(
        name="SIEM",
        auth_type="header",
        header_name="X-Api-Key",
        auth_secret="token ABC/+==",
        signing_secret="unicode-signing-secret-" + "é" * 32,
    )
    assert profile.auth_secret == "token ABC/+=="
    assert profile.signing_secret.endswith("é" * 32)

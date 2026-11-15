"""New browser credentials must survive native password-input normalization."""

import pytest
from pydantic import ValidationError

from app.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest
from app.schemas.user import UserCreateRequest, UserUpdateRequest


NEW_PASSWORD_FIELDS = (
    pytest.param(RegisterRequest, "password", id="registration"),
    pytest.param(ChangePasswordRequest, "new_password", id="password-change"),
    pytest.param(UserCreateRequest, "password", id="admin-create"),
    pytest.param(UserUpdateRequest, "password", id="admin-reset"),
)


def _payload(model, field, password):
    payload = {field: password}
    if model in {RegisterRequest, UserCreateRequest}:
        payload["email"] = "analyst@example.com"
    if model is ChangePasswordRequest:
        payload["current_password"] = "ExistingPassword123!"
    return payload


@pytest.mark.parametrize(("model", "field"), NEW_PASSWORD_FIELDS)
@pytest.mark.parametrize("separator", ["\r", "\n", "\r\n"])
@pytest.mark.parametrize("position", ["leading", "embedded", "trailing"])
def test_new_password_rejects_browser_stripped_line_breaks(
    model, field, separator, position
):
    password = {
        "leading": separator + "NewPassword123!",
        "embedded": "NewPassword" + separator + "123!",
        "trailing": "NewPassword123!" + separator,
    }[position]

    with pytest.raises(ValidationError) as raised:
        model.model_validate(_payload(model, field, password))

    assert [issue["loc"] for issue in raised.value.errors(include_input=False)] == [
        (field,)
    ]


@pytest.mark.parametrize(("model", "field"), NEW_PASSWORD_FIELDS)
@pytest.mark.parametrize(
    "password",
    ["a" * 8, "a" * 256, "é" * 256, "  keep spaces  ", "密码Δ🔐abcde"],
)
def test_new_password_preserves_valid_content_and_existing_character_bounds(
    model, field, password
):
    parsed = model.model_validate(_payload(model, field, password))

    assert getattr(parsed, field) == password


@pytest.mark.parametrize(("model", "field"), NEW_PASSWORD_FIELDS)
@pytest.mark.parametrize("password", ["a" * 7, "a" * 257])
def test_new_password_keeps_existing_length_rejections(model, field, password):
    with pytest.raises(ValidationError) as raised:
        model.model_validate(_payload(model, field, password))

    assert [issue["loc"] for issue in raised.value.errors(include_input=False)] == [
        (field,)
    ]


@pytest.mark.parametrize("separator", ["\r", "\n", "\r\n"])
def test_existing_password_line_breaks_remain_usable_for_api_login_and_change(separator):
    existing = "Existing" + separator + "Password123!"

    assert LoginRequest(email="analyst@example.com", password=existing).password == existing
    assert (
        ChangePasswordRequest(
            current_password=existing, new_password="NewPassword123!"
        ).current_password
        == existing
    )


@pytest.mark.parametrize("payload", [{}, {"password": None}])
def test_admin_update_can_still_omit_a_password_change(payload):
    assert UserUpdateRequest.model_validate(payload).password is None

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.api_errors import ApiHTTPException, install_api_error_handlers


class _Payload(BaseModel):
    count: int


def _test_app() -> FastAPI:
    application = FastAPI()
    install_api_error_handlers(application)

    @application.get("/missing")
    def missing():
        raise HTTPException(status_code=404, detail="Diagnostic object not found")

    @application.post("/validate")
    def validate(payload: _Payload):
        return payload

    @application.get("/version-conflict")
    def version_conflict():
        raise ApiHTTPException(
            status_code=409,
            detail="The resource changed after it was loaded.",
            error_code="resource_version_conflict",
            error_context={"current_version": 7, "reload_required": True},
        )

    @application.get("/boom")
    def boom():
        raise RuntimeError("database_url=postgresql://user:super-secret@db/threatlens")

    return application


def test_http_errors_keep_detail_and_add_diagnostic_envelope():
    with TestClient(_test_app()) as client:
        response = client.get("/missing")

    payload = response.json()
    assert response.status_code == 404
    assert payload["detail"] == "Diagnostic object not found"
    assert payload["error"] == {
        "code": "not_found",
        "message": "Diagnostic object not found",
        "request_id": response.headers["x-request-id"],
        "status": 404,
        "retryable": False,
    }


def test_validation_errors_do_not_echo_submitted_input():
    with TestClient(_test_app()) as client:
        response = client.post("/validate", json={"count": "secret-input"})

    payload = response.json()
    assert response.status_code == 422
    assert payload["error"]["code"] == "validation_error"
    assert "count" in payload["error"]["message"]
    assert "secret-input" not in response.text
    assert set(payload["detail"][0]) <= {"type", "loc", "msg"}


def test_coded_http_errors_preserve_detail_and_use_stable_error_code():
    with TestClient(_test_app()) as client:
        response = client.get("/version-conflict")

    payload = response.json()
    assert response.status_code == 409
    assert payload["detail"] == "The resource changed after it was loaded."
    assert payload["error"]["code"] == "resource_version_conflict"
    assert payload["error"]["message"] == payload["detail"]
    assert payload["error"]["context"] == {
        "current_version": 7,
        "reload_required": True,
    }


def test_unexpected_errors_return_safe_reference_without_exception_details():
    with TestClient(_test_app(), raise_server_exceptions=False) as client:
        response = client.get("/boom")

    payload = response.json()
    assert response.status_code == 500
    assert payload["detail"] == "The server could not complete the request."
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["request_id"] == response.headers["x-request-id"]
    assert "super-secret" not in response.text

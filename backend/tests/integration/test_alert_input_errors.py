import json

import pytest


@pytest.mark.parametrize("unsafe", ["watch\x00list", "watch\ud800list"])
def test_watchlist_invalid_storage_text_returns_actionable_validation_error(
    client, auth_headers, unsafe
):
    response = client.post(
        "/alerts",
        headers={**auth_headers["analyst"], "content-type": "application/json"},
        content=json.dumps({"name": unsafe, "category": "other", "keywords": ["test"]}),
    )
    assert response.status_code == 422
    issue = response.json()["detail"][0]
    assert issue["loc"] == ["body", "name"]
    assert "cannot be stored" in issue["msg"] or "unicode string" in issue["msg"]
    assert "input" not in issue
    # Rejecting malformed input must not poison a session or the next write.
    valid = client.post(
        "/alerts",
        headers=auth_headers["analyst"],
        json={"name": "Valid after rejection", "category": "other", "keywords": ["test"]},
    )
    assert valid.status_code == 201

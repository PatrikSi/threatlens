"""Publication receiver credentials cannot be ambiguous or leak through URLs."""

import uuid

import pytest

from tests.integration.test_indicator_publications import reviewed as reviewed_fixture
from tests.integration.test_indicator_intelligence import intel_setup  # noqa: F401
from tests.integration.test_publication_distribution import register


reviewed = reviewed_fixture


@pytest.mark.parametrize("credential_location", ["duplicate", "query"])
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "status", None),
        ("GET", "changes", None),
        (
            "POST",
            "acknowledgements",
            {"generation": 1, "change_ids": [str(uuid.uuid4())]},
        ),
        (
            "POST",
            "reset",
            {"expected_generation": 1, "discarded_previous_publications": True},
        ),
    ],
)
def test_receiver_controls_reject_ambiguous_and_query_bearers(
    client, reviewed, auth_headers, credential_location, method, path, payload
):  # noqa: F811
    _, _, headers = register(client, reviewed, auth_headers)
    pairs = list(headers.items())
    params = {}
    if credential_location == "duplicate":
        pairs.append(("Authorization", "Bearer tlpc_invalid_credential"))
    else:
        params["access_token"] = headers["Authorization"].removeprefix("Bearer ")
    response = client.request(
        method,
        f"/publication-distribution/{path}",
        headers=pairs,
        params=params,
        json=payload,
    )
    assert response.status_code == 401, response.text
    assert "consumer_bearer_required" in response.text
    assert headers["Authorization"] not in response.text

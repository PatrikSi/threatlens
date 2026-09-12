import json

import pytest

from app.services.ai_output_storage import AIOutputStorageError, validate_output_storage
from app.services.ai_provider_client import AIIntegrationError
from tests.unit.test_ai_provider_result_contracts import _call


@pytest.mark.parametrize("payload", [
    {"body": "\x00"}, {"\x00": "body"}, {"body": "\ud800"}, {"body": "\udfff"},
    {"extra": float("nan")}, {"extra": float("inf")}, {"extra": object()}, {1: "body"},
])
def test_storage_rejects_invalid_values_in_every_field(payload):
    with pytest.raises(AIOutputStorageError):
        validate_output_storage(payload, max_bytes=4096)


def test_storage_bounds_depth_nodes_and_serialized_bytes():
    nested = {"body": "Valid"}
    for _ in range(33):
        nested = {"extra": nested}
    for payload, limit in [(nested, 10000), ([0] * 100001, 1000000), ({"body": "x" * 50}, 40)]:
        with pytest.raises(AIOutputStorageError):
            validate_output_storage(payload, max_bytes=limit)
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(AIOutputStorageError):
        validate_output_storage(cyclic, max_bytes=4096)
    payload = {"body": "Café 中文 😀\n\t", "amount": 0.5, "present": True, "empty": None}
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    validate_output_storage(payload, max_bytes=len(encoded))
    with pytest.raises(AIOutputStorageError):
        validate_output_storage(payload, max_bytes=len(encoded) - 1)


def test_excessively_nested_received_content_preserves_usage_and_io_state():
    content = '{"extra":' + '[' * 20000 + '0' + ']' * 20000 + '}'
    with pytest.raises(AIIntegrationError) as caught:
        _call({"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 25}})
    assert caught.value.provider_io_outcome == "response_received"
    assert caught.value.failure_category == "invalid_json"
    assert caught.value.total_tokens == 25

from app.services.ai_provider_exchange import sanitize_provider_exchange, summarize_response_json


def test_sanitize_provider_exchange_summarizes_request_and_response_without_raw_payloads():
    response_body = '{"id":"resp-1","choices":[{"finish_reason":"stop"}]}'
    payload = sanitize_provider_exchange(
        request_url="https://api.example.com/v1/chat/completions",
        request_payload={
            "model": "gpt-test",
            "messages": [
                {"role": "system", "content": "Summarize threats"},
                {"role": "user", "content": [{"text": "Alpha"}, {"content": "Beta"}]},
            ],
            "temperature": 0.4,
            "max_tokens": 700,
        },
        response_body=response_body,
        response_json={
            "model": "gpt-test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "choices": [{"finish_reason": "stop"}],
            "error": {"type": "invalid_request", "message": "too long"},
        },
        status_code=429,
        finish_reason="length",
    )

    assert payload["request_host"] == "api.example.com"
    assert payload["request_message_count"] == 2
    assert payload["request_message_roles"] == ["system", "user"]
    assert payload["request_prompt_chars"] == len("Summarize threatsAlphaBeta")
    assert payload["request_model"] == "gpt-test"
    assert payload["request_temperature"] == 0.4
    assert payload["request_max_tokens"] == 700
    assert payload["status_code"] == 429
    assert payload["finish_reason"] == "length"
    assert payload["response_body_chars"] == len(response_body)
    assert "response_body_sha256" in payload
    assert payload["response_json_summary"]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert payload["response_json_summary"]["error"] == {
        "type": "invalid_request",
        "message_chars": len("too long"),
    }
    assert "request_payload" not in payload
    assert "response_body" not in payload


def test_summarize_response_json_handles_lists_and_scalars():
    assert summarize_response_json([{"id": 1}, {"id": 2}]) == {"type": "list", "length": 2}
    assert summarize_response_json("plain-text") == {"type": "str"}

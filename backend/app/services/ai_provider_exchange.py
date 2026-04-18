from __future__ import annotations

import hashlib
from urllib.parse import urlsplit


def build_provider_exchange_payload(
    *,
    request_url: str | None,
    request_payload: dict[str, object] | None,
    response_body: str | None,
    response_json: object | None,
    status_code: int | None,
    finish_reason: str | None,
) -> dict[str, object]:
    return sanitize_provider_exchange(
        request_url=request_url,
        request_payload=request_payload,
        response_body=response_body,
        response_json=response_json,
        status_code=status_code,
        finish_reason=finish_reason,
    )


def sanitize_provider_exchange(
    *,
    request_url: str | None,
    request_payload: dict[str, object] | None,
    response_body: str | None,
    response_json: object | None,
    status_code: int | None,
    finish_reason: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if request_url:
        payload.update(summarize_request_url(request_url))
    if request_payload is not None:
        payload.update(summarize_request_payload(request_payload))
    if status_code is not None:
        payload["status_code"] = status_code
    if finish_reason:
        payload["finish_reason"] = finish_reason
    if response_body is not None:
        payload["response_body_chars"] = len(response_body)
        payload["response_body_sha256"] = hashlib.sha256(response_body.encode("utf-8", errors="ignore")).hexdigest()
    if response_json is not None:
        payload["response_json_summary"] = summarize_response_json(response_json)
    return payload


def summarize_request_url(request_url: str) -> dict[str, object]:
    parsed = urlsplit(request_url)
    return {
        "request_url": request_url,
        "request_scheme": parsed.scheme.lower(),
        "request_host": (parsed.hostname or "").lower(),
        "request_path": parsed.path or "/",
    }


def summarize_request_payload(request_payload: dict[str, object]) -> dict[str, object]:
    messages = request_payload.get("messages")
    message_roles: list[str] = []
    prompt_char_count = 0
    if isinstance(messages, list):
        for entry in messages:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            if isinstance(role, str):
                message_roles.append(role)
            content = entry.get("content")
            if isinstance(content, str):
                prompt_char_count += len(content)
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    text_value = part.get("text") or part.get("content")
                    if isinstance(text_value, str):
                        prompt_char_count += len(text_value)

    summary: dict[str, object] = {
        "request_message_count": len(messages) if isinstance(messages, list) else 0,
        "request_message_roles": message_roles,
        "request_prompt_chars": prompt_char_count,
    }
    model = request_payload.get("model")
    if isinstance(model, str):
        summary["request_model"] = model
    temperature = request_payload.get("temperature")
    if isinstance(temperature, (int, float)):
        summary["request_temperature"] = float(temperature)
    max_tokens = request_payload.get("max_tokens")
    if isinstance(max_tokens, int):
        summary["request_max_tokens"] = max_tokens
    return summary


def summarize_response_json(response_json: object) -> dict[str, object]:
    if isinstance(response_json, dict):
        summary: dict[str, object] = {
            "top_level_keys": sorted(str(key) for key in response_json.keys())[:20],
        }
        model = response_json.get("model")
        if isinstance(model, str):
            summary["response_model"] = model
        usage = response_json.get("usage")
        if isinstance(usage, dict):
            usage_summary: dict[str, int] = {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    usage_summary[key] = value
            if usage_summary:
                summary["usage"] = usage_summary
        choices = response_json.get("choices")
        if isinstance(choices, list):
            summary["choices_count"] = len(choices)
            if choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get("finish_reason")
                if isinstance(finish_reason, str):
                    summary["first_choice_finish_reason"] = finish_reason
        error = response_json.get("error")
        if isinstance(error, dict):
            error_summary: dict[str, object] = {}
            for key in ("type", "code", "param"):
                value = error.get(key)
                if value is not None:
                    error_summary[key] = value
            message = error.get("message")
            if isinstance(message, str):
                error_summary["message_chars"] = len(message)
            if error_summary:
                summary["error"] = error_summary
        return summary
    if isinstance(response_json, list):
        return {"type": "list", "length": len(response_json)}
    return {"type": type(response_json).__name__}

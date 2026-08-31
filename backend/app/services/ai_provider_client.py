from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import get_settings
from app.services.ai_config import ActiveAISettings, is_shared_ai_base_url_allowed
from app.services.ai_normalization import coerce_optional_int, normalize_optional_text
from app.services.ai_provider_exchange import sanitize_provider_exchange
from app.services.safe_fetch import SafeFetchError


AIProviderIOOutcome = Literal["not_sent", "response_received", "ambiguous"]
AI_PROVIDER_IO_NOT_SENT: AIProviderIOOutcome = "not_sent"
AI_PROVIDER_IO_RESPONSE_RECEIVED: AIProviderIOOutcome = "response_received"
AI_PROVIDER_IO_AMBIGUOUS: AIProviderIOOutcome = "ambiguous"


class AIIntegrationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        request_url: str | None = None,
        request_payload: dict[str, object] | None = None,
        response_body: str | None = None,
        response_json: object | None = None,
        status_code: int | None = None,
        retry_hint: str | None = None,
        retryable: bool = False,
        provider_io_outcome: AIProviderIOOutcome = AI_PROVIDER_IO_AMBIGUOUS,
    ):
        super().__init__(message)
        self.request_url = request_url
        self.request_payload = request_payload
        self.response_body = response_body
        self.response_json = response_json
        self.status_code = status_code
        self.retry_hint = retry_hint
        self.retryable = retryable
        self.provider_io_outcome = provider_io_outcome
        self.attempt_count = 1

    def debug_payload(self) -> dict[str, object]:
        payload = sanitize_provider_exchange(
            request_url=self.request_url,
            request_payload=self.request_payload,
            response_body=self.response_body,
            response_json=self.response_json,
            status_code=self.status_code,
            finish_reason=None,
        )
        if self.retry_hint:
            payload["retry_hint"] = self.retry_hint
        payload["retryable"] = self.retryable
        payload["provider_io_outcome"] = self.provider_io_outcome
        return payload


@dataclass(frozen=True)
class AICompletionResult:
    payload: dict[str, object]
    provider: str
    model: str | None
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    prompt_char_count: int = 0
    response_char_count: int = 0
    request_url: str | None = None
    request_payload: dict[str, object] | None = None
    response_body: str | None = None
    response_json: object | None = None
    status_code: int | None = None
    finish_reason: str | None = None
    attempt_count: int = 1


def call_ai_json(
    active: ActiveAISettings,
    *,
    messages: list[dict[str, str]],
    client_factory: Callable[..., Any],
    max_completion_tokens: int | None = None,
) -> AICompletionResult:
    if not active.ai_enabled:
        raise AIIntegrationError(
            "AI features are disabled",
            retryable=False,
            provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
        )
    if not is_shared_ai_base_url_allowed(active.base_url, api_key=active.api_key):
        raise AIIntegrationError(
            "AI base URL is not allowed when the server AI_API_KEY is configured",
            retryable=False,
            provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
        )
    if not active.ai_configured or not active.base_url or not active.model:
        raise AIIntegrationError(
            "AI settings are incomplete",
            retryable=False,
            provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
        )

    request_url = build_chat_completion_url(active.base_url)
    request_payload = {
        "model": active.model,
        "messages": messages,
        "temperature": active.temperature,
        "max_tokens": max_completion_tokens if max_completion_tokens is not None else active.max_completion_tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if active.api_key:
        headers["Authorization"] = f"Bearer {active.api_key}"

    started_at = time.perf_counter()
    runtime_settings = get_settings()
    timeout = httpx.Timeout(
        connect=active.request_timeout_seconds,
        read=active.request_timeout_seconds,
        write=active.request_timeout_seconds,
        pool=active.request_timeout_seconds,
    )
    try:
        with client_factory(
            timeout=timeout,
            headers={"User-Agent": runtime_settings.fetch_user_agent},
            allow_private_network=runtime_settings.allow_private_network_ai,
        ) as client:
            response = client.post(request_url, headers=headers, json=request_payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        response_body = exc.response.text
        try:
            response_json: object | None = exc.response.json()
        except ValueError:
            response_json = None
        provider_error_message = extract_provider_error_message(response_json)
        raise AIIntegrationError(
            provider_error_message or f"AI request failed: {exc}",
            request_url=request_url,
            request_payload=request_payload,
            response_body=response_body,
            response_json=response_json,
            status_code=exc.response.status_code,
            retryable=False
            if looks_like_provider_auth_error(provider_error_message)
            else ai_status_code_is_retryable(exc.response.status_code),
            provider_io_outcome=AI_PROVIDER_IO_RESPONSE_RECEIVED,
        ) from exc
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
        httpx.InvalidURL,
        httpx.UnsupportedProtocol,
        SafeFetchError,
    ) as exc:
        raise AIIntegrationError(
            f"AI request failed: {exc}",
            request_url=request_url,
            request_payload=request_payload,
            retryable=True,
            provider_io_outcome=AI_PROVIDER_IO_NOT_SENT,
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise AIIntegrationError(
            f"AI request outcome is unknown: {exc}",
            request_url=request_url,
            request_payload=request_payload,
            retryable=False,
            provider_io_outcome=AI_PROVIDER_IO_AMBIGUOUS,
        ) from exc

    latency_ms = int((time.perf_counter() - started_at) * 1000)
    response_body = response.text
    try:
        payload = response.json()
    except ValueError as exc:
        raise AIIntegrationError(
            "AI endpoint returned non-JSON output",
            request_url=request_url,
            request_payload=request_payload,
            response_body=response_body,
            status_code=response.status_code,
            retryable=True,
            provider_io_outcome=AI_PROVIDER_IO_RESPONSE_RECEIVED,
        ) from exc
    provider_error_message = extract_provider_error_message(payload)
    if provider_error_message:
        raise AIIntegrationError(
            provider_error_message,
            request_url=request_url,
            request_payload=request_payload,
            response_body=response_body,
            response_json=payload,
            status_code=response.status_code,
            retryable=not looks_like_provider_auth_error(provider_error_message),
            provider_io_outcome=AI_PROVIDER_IO_RESPONSE_RECEIVED,
        )

    try:
        choice = payload["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIIntegrationError(
            "AI endpoint returned an unexpected response shape",
            request_url=request_url,
            request_payload=request_payload,
            response_body=response_body,
            response_json=payload,
            status_code=response.status_code,
            retryable=True,
            provider_io_outcome=AI_PROVIDER_IO_RESPONSE_RECEIVED,
        ) from exc

    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    try:
        content = extract_message_content(message.get("content"))
    except AIIntegrationError as exc:
        raise AIIntegrationError(
            str(exc),
            request_url=request_url,
            request_payload=request_payload,
            response_body=response_body,
            response_json=payload,
            status_code=response.status_code,
            retryable=True,
            provider_io_outcome=AI_PROVIDER_IO_RESPONSE_RECEIVED,
        ) from exc
    try:
        parsed = parse_ai_json_content(content)
    except AIIntegrationError as exc:
        message_text = str(exc)
        retry_hint = None
        if finish_reason == "length":
            message_text = "AI response was truncated by max_tokens before returning valid JSON"
            retry_hint = "expand_completion_budget"
        raise AIIntegrationError(
            message_text,
            request_url=request_url,
            request_payload=request_payload,
            response_body=response_body,
            response_json=payload,
            status_code=response.status_code,
            retry_hint=retry_hint,
            retryable=True,
            provider_io_outcome=AI_PROVIDER_IO_RESPONSE_RECEIVED,
        ) from exc
    usage = payload.get("usage") or {}
    prompt_char_count = sum(len(entry.get("content") or "") for entry in messages)
    return AICompletionResult(
        payload=parsed,
        provider=active.provider_type,
        model=payload.get("model") or active.model,
        latency_ms=latency_ms,
        prompt_tokens=coerce_optional_int(usage.get("prompt_tokens")),
        completion_tokens=coerce_optional_int(usage.get("completion_tokens")),
        total_tokens=coerce_optional_int(usage.get("total_tokens")),
        prompt_char_count=prompt_char_count,
        response_char_count=len(content),
        request_url=request_url,
        request_payload=request_payload,
        response_body=response_body,
        response_json=payload,
        status_code=response.status_code,
        finish_reason=finish_reason if isinstance(finish_reason, str) else None,
    )


def build_chat_completion_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return f"{cleaned}/chat/completions"
    if parsed.scheme and parsed.netloc and parsed.path in {"", "/"}:
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1/chat/completions", "", ""))
    return f"{cleaned}/chat/completions"


def ai_status_code_is_retryable(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or 500 <= status_code <= 599


def extract_provider_error_message(payload: object | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    raw_error = payload.get("error")
    if isinstance(raw_error, str):
        return normalize_optional_text(raw_error)
    if isinstance(raw_error, dict):
        for key in ("message", "detail", "error"):
            value = normalize_optional_text(raw_error.get(key))
            if value:
                return value
    for key in ("message", "detail"):
        value = normalize_optional_text(payload.get(key))
        if value and "choices" not in payload:
            return value
    return None


def looks_like_provider_auth_error(message: str | None) -> bool:
    lowered = (message or "").lower()
    return any(
        fragment in lowered
        for fragment in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "authentication required",
            "api key",
            "invalid key",
            "invalid token",
            "credentials",
        )
    )


def extract_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, dict):
                text_value = entry.get("text") or entry.get("content")
                if isinstance(text_value, str):
                    parts.append(text_value)
        return "\n".join(part for part in parts if part)
    raise AIIntegrationError("AI endpoint did not return text content")


def parse_ai_json_content(content: str) -> dict[str, object]:
    candidate = strip_code_fence_wrapper(content.strip())
    try:
        parsed = json.loads(candidate)
    except ValueError as exc:
        recovered = extract_first_json_object(candidate)
        if recovered is None:
            recovered = repair_unclosed_json_object(candidate)
        if recovered is None:
            raise AIIntegrationError("AI response did not contain valid JSON") from exc
        parsed = recovered
    if not isinstance(parsed, dict):
        raise AIIntegrationError("AI response JSON must be an object")
    return parsed


def strip_code_fence_wrapper(candidate: str) -> str:
    if not candidate.startswith("```"):
        return candidate

    lines = candidate.splitlines()
    if not lines:
        return candidate
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_first_json_object(candidate: str) -> dict[str, object] | None:
    start = candidate.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char != "}":
            continue
        depth -= 1
        if depth != 0:
            continue
        try:
            parsed = json.loads(candidate[start : index + 1])
        except ValueError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed
    return None


def repair_unclosed_json_object(candidate: str) -> dict[str, object] | None:
    start = candidate.find("{")
    if start == -1:
        return None

    depth, in_string = scan_json_object_balance(candidate, start=start)
    if depth <= 0 or in_string:
        return None

    repaired = candidate + ("}" * depth)
    try:
        parsed = json.loads(repaired)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def scan_json_object_balance(candidate: str, *, start: int) -> tuple[int, bool]:
    depth = 0
    in_string = False
    escape = False
    for char in candidate[start:]:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
    return depth, in_string

"""Shared URL syntax and credential-destination rules for AI integrations.

This module is independent of settings and persistence: only the operator may
choose the destination trusted to receive the legacy environment credential.
"""

from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

GEMINI_COMPATIBLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_AI_API_KEY_BASE_URL = "https://api.openai.com"


def parse_ai_endpoint(value: str) -> SplitResult:
    """Parse an endpoint without letting URL normalization hide unsafe syntax."""
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError(
            "AI endpoint must not contain whitespace, controls or backslashes."
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("AI endpoint must be a valid URL.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or port == 0:
        raise ValueError(
            "AI endpoint must use http or https and include a valid host and port."
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("AI endpoint must not contain embedded credentials.")
    if "?" in value or "#" in value:
        raise ValueError("AI endpoint must not contain query parameters or fragments.")
    if any(char in parsed.netloc for char in "%{}"):
        raise ValueError("AI endpoint host must not contain templates or URL escapes.")
    return parsed


def ai_endpoint_origin(value: str) -> tuple[str, str, int]:
    parsed = parse_ai_endpoint(value)
    return (
        parsed.scheme,
        (parsed.hostname or "").lower().rstrip("."),
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def validate_ai_key_base_url(value: str) -> str:
    cleaned = value.strip()
    parsed = parse_ai_endpoint(cleaned)
    if parsed.scheme != "https":
        raise ValueError(
            "AI_API_KEY_BASE_URL must use HTTPS to protect the environment API key."
        )
    return cleaned.rstrip("/")


def matches_ai_key_origin(base_url: str | None, key_base_url: str) -> bool:
    if not base_url:
        return False
    try:
        origin = ai_endpoint_origin(base_url)
        return origin[0] == "https" and origin == ai_endpoint_origin(key_base_url)
    except ValueError:
        return False


def validate_chat_completion_endpoint(base_url: str) -> SplitResult:
    parsed = parse_ai_endpoint(base_url)
    path = unquote(parsed.path).rstrip("/").lower()
    if path.endswith((":generatecontent", ":streamgeneratecontent")):
        raise ValueError(
            "This is a native Gemini generateContent endpoint, not an OpenAI-compatible endpoint. "
            f"Use {GEMINI_COMPATIBLE_BASE_URL} as the base URL and enter the model separately."
        )
    return parsed


def chat_completion_url(base_url: str) -> str:
    parsed = validate_chat_completion_endpoint(base_url)
    path = parsed.path.rstrip("/")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname == "generativelanguage.googleapis.com" and path in {"", "/v1beta"}:
        path = "/v1beta/openai"
    if not path:
        path = "/v1"
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

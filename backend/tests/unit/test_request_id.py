from app import main


def test_normalize_request_id_preserves_valid_value():
    request_id = "trace-abc_123.DEF"
    assert main._normalize_request_id(request_id) == request_id


def test_normalize_request_id_strips_disallowed_characters():
    assert main._normalize_request_id("abc\r\n123\t!@#") == "abc123"


def test_normalize_request_id_caps_length():
    long_value = "a" * 300
    assert len(main._normalize_request_id(long_value)) == 128


def test_normalize_request_id_generates_fallback_for_empty_or_invalid(monkeypatch):
    monkeypatch.setattr(main.uuid, "uuid4", lambda: "generated-id")

    assert main._normalize_request_id("") == "generated-id"
    assert main._normalize_request_id(" \t ") == "generated-id"
    assert main._normalize_request_id("!!!") == "generated-id"

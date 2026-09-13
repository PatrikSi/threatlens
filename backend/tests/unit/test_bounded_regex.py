import os
import re
import time

import pytest

from app.services import bounded_regex
from app.services.bounded_regex import RegexRule, evaluate_regex_batch, validate_regex


@pytest.mark.parametrize("pattern,text,case_sensitive", [
    (r"(?<=vendor:)fortinet\b", "vendor:Fortinet fixes a bug", False),
    (r"(?P<word>\w+)\s+(?P=word)", "repeat repeat", True),
    (r"é\w+", "École", False),
    (r"\bAPT\d+\b", "apt29 activity", True),
    (r"a(?=b)", "ab", True),
])
def test_isolated_evaluator_preserves_python_regex_semantics(pattern, text, case_sensitive):
    result = evaluate_regex_batch([RegexRule(pattern, case_sensitive, ["title"])], {"title": text})[0]
    expected = bool(re.search(pattern, text, 0 if case_sensitive else re.IGNORECASE))
    assert result.error is None
    assert result.sections == (["title"] if expected else [])


def test_catastrophic_expression_times_out_and_next_rule_still_runs():
    start = time.monotonic()
    results = evaluate_regex_batch([
        RegexRule(r"(a+)+$", True, ["article_text"]),
        RegexRule("safe", False, ["title"]),
    ], {"article_text": "a" * 20_000 + "!", "title": "Safe item"})
    assert results[0].error == "timeout"
    assert results[0].sections == []
    assert results[1].sections == ["title"]
    assert time.monotonic() - start < 1.5


def test_many_expensive_rules_share_a_batch_budget():
    start = time.monotonic()
    results = evaluate_regex_batch(
        [RegexRule(r"(a+)+$", True, ["title"]) for _ in range(40)],
        {"title": "a" * 10_000 + "!"},
    )
    assert all(result.error in {"timeout", "budget_exhausted"} for result in results)
    assert any(result.error == "budget_exhausted" for result in results)
    assert time.monotonic() - start < 1.5


def test_oversized_text_is_rejected_before_starting_a_process(monkeypatch):
    monkeypatch.setattr(bounded_regex.subprocess, "run", lambda *args, **kwargs: pytest.fail("process must not start"))
    result = evaluate_regex_batch([RegexRule("needle", False, ["title"])],
                                  {"title": "x" * (bounded_regex.MAX_REGEX_TEXT_CHARS + 1)})[0]
    assert result.error == "input_too_large"


def test_validation_rejects_invalid_and_excessively_nested_patterns():
    assert validate_regex("[", False) == "invalid_pattern"
    assert validate_regex("(" * 1500 + "a" + ")" * 1500, False) == "invalid_pattern"
    assert validate_regex("a{100000000000000000000}", False) == "invalid_pattern"


def test_parent_timeout_kills_and_reaps_an_unresponsive_child(tmp_path, monkeypatch):
    pid_file = tmp_path / "child.pid"
    worker = tmp_path / "stalled.py"
    worker.write_text(f"import os,time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)\n")
    monkeypatch.setattr(bounded_regex, "_WORKER", worker)
    monkeypatch.setattr(bounded_regex, "REGEX_PROCESS_TIMEOUT_SECONDS", 0.2)
    result = evaluate_regex_batch([RegexRule("ok", False, [])], {})[0]
    assert result.error == "worker_timeout"
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_child_does_not_inherit_application_environment(tmp_path, monkeypatch):
    worker = tmp_path / "environment.py"
    worker.write_text("import os,json\nassert 'APP_DATA_ENCRYPTION_KEY' not in os.environ\n"
                      "print(json.dumps([{'sections':[], 'error':None}]))\n")
    monkeypatch.setattr(bounded_regex, "_WORKER", worker)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "synthetic-test-only")
    assert evaluate_regex_batch([RegexRule("ok", False, [])], {})[0].error is None


def test_invalid_worker_result_fails_closed(tmp_path, monkeypatch):
    worker = tmp_path / "invalid.py"
    worker.write_text("print('[{\"sections\":[\"not-a-selected-field\"],\"error\":null}]')\n")
    monkeypatch.setattr(bounded_regex, "_WORKER", worker)
    result = evaluate_regex_batch([RegexRule("ok", False, ["title"])], {})[0]
    assert result.sections == []
    assert result.error == "worker_unavailable"


def test_rule_count_is_bounded_and_excess_is_explicit():
    results = evaluate_regex_batch([RegexRule("a", True, ["title"])] * 202, {"title": "a"})
    assert all(result.sections == ["title"] for result in results[:200])
    assert [result.error for result in results[200:]] == ["budget_exhausted"] * 2

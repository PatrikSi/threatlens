"""Execute custom regexes outside API/worker processes with explicit budgets."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_REGEX_RULES = 200
MAX_REGEX_TEXT_CHARS = 2_000_000
MAX_REGEX_INPUT_BYTES = 16_000_000
REGEX_PROCESS_TIMEOUT_SECONDS = 1.0
_WORKER = Path(__file__).with_name("regex_worker.py")

ERROR_MESSAGES = {
    "timeout": "The regular expression exceeded its 50 ms execution budget. Simplify the pattern.",
    "budget_exhausted": "The combined regular-expression budget was exhausted. Reduce or simplify enabled rules.",
    "input_too_large": "The selected text exceeds the regular-expression input budget of 2,000,000 characters.",
    "invalid_pattern": "The regular expression is invalid or exceeds supported compiler limits.",
    "resource_limit": "The regular expression exceeded the isolated process resource budget.",
    "worker_unavailable": "The isolated regular-expression evaluator is unavailable. Try again later.",
    "worker_timeout": "The isolated regular-expression evaluator did not finish within one second. Retry or simplify enabled rules.",
}


@dataclass(frozen=True)
class RegexRule:
    pattern: str
    case_sensitive: bool
    fields: list[str]


@dataclass(frozen=True)
class RegexResult:
    sections: list[str]
    error: str | None = None


def evaluate_regex_batch(rules: list[RegexRule], texts: dict[str, str]) -> list[RegexResult]:
    if not rules:
        return []
    selected = rules[:MAX_REGEX_RULES]
    bounded_texts = {
        field: value[:MAX_REGEX_TEXT_CHARS + 1]
        for field, value in texts.items()
        if any(field in rule.fields for rule in selected)
    }
    if sum(map(len, bounded_texts.values())) > MAX_REGEX_TEXT_CHARS:
        return [RegexResult([], "input_too_large") for _ in rules]
    payload = json.dumps({
        "rules": [vars(rule) for rule in selected], "texts": bounded_texts,
    }, ensure_ascii=True).encode("ascii")
    if len(payload) > MAX_REGEX_INPUT_BYTES:
        return [RegexResult([], "input_too_large") for _ in rules]
    results = _run_isolated(selected, payload)
    results.extend(RegexResult([], "budget_exhausted") for _ in rules[MAX_REGEX_RULES:])
    return results


def _run_isolated(rules: list[RegexRule], payload: bytes) -> list[RegexResult]:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", str(_WORKER)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=REGEX_PROCESS_TIMEOUT_SECONDS,
            check=True,
            close_fds=True,
            cwd="/",
            env={"LANG": "C.UTF-8"},
        )
        raw = json.loads(completed.stdout)
        if not isinstance(raw, list) or len(raw) != len(rules):
            raise ValueError("invalid evaluator response")
        return [_parse_result(value, rule) for value, rule in zip(raw, rules, strict=True)]
    except subprocess.TimeoutExpired:
        # subprocess.run kills AND reaps before raising; no regex remains running.
        code = "worker_timeout"
    except subprocess.CalledProcessError:
        code = "resource_limit"
    except (OSError, ValueError, TypeError, KeyError):
        code = "worker_unavailable"
    return [RegexResult([], code) for _ in rules]


def _parse_result(value, rule: RegexRule) -> RegexResult:
    sections, error = value["sections"], value["error"]
    if error is not None and error not in ERROR_MESSAGES:
        raise ValueError("unknown evaluator error")
    if not isinstance(sections, list) or any(field not in rule.fields for field in sections):
        raise ValueError("invalid matched section")
    return RegexResult(sections if error is None else [], error)


def validate_regex(pattern: str, case_sensitive: bool) -> str | None:
    return evaluate_regex_batch([RegexRule(pattern, case_sensitive, [])], {})[0].error

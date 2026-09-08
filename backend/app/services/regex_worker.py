"""Standalone bounded Python-regex process; invoke only through bounded_regex.

This file intentionally imports no application modules or third-party packages.
The parent starts it with isolated Python, a clean environment, and closed FDs.
"""
from __future__ import annotations

import json
import re
import resource
import signal
import sys
import time

MAX_INPUT_BYTES = 16_000_000
MAX_RULES = 200
MAX_TEXT_CHARS = 2_000_000
RULE_SECONDS = 0.05
BATCH_SECONDS = 0.4
MEMORY_BYTES = 256 * 1024 * 1024


def _expired(_signum, _frame):
    raise TimeoutError


def _evaluate(rule, texts, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"sections": [], "error": "budget_exhausted"}
    fields = rule["fields"]
    if sum(len(texts.get(field, "")) for field in fields) > MAX_TEXT_CHARS:
        return {"sections": [], "error": "input_too_large"}
    signal.setitimer(signal.ITIMER_REAL, min(RULE_SECONDS, remaining))
    try:
        # Compilation and every selected field share the same interrupt timer.
        # Parent termination is the fallback if native code cannot be interrupted.
        compiled = re.compile(rule["pattern"], 0 if rule["case_sensitive"] else re.IGNORECASE)
        sections = [field for field in fields if texts.get(field) and compiled.search(texts[field])]
        return {"sections": sections, "error": None}
    except TimeoutError:
        return {"sections": [], "error": "timeout"}
    except (re.error, RecursionError, OverflowError):
        return {"sections": [], "error": "invalid_pattern"}
    except MemoryError:
        return {"sections": [], "error": "resource_limit"}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    signal.signal(signal.SIGALRM, _expired)
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input budget exceeded")
    request = json.loads(raw)
    rules = request["rules"]
    texts = request["texts"]
    if len(rules) > MAX_RULES or any(len(rule["pattern"]) > 4000 for rule in rules):
        raise ValueError("rule budget exceeded")
    deadline = time.monotonic() + BATCH_SECONDS
    results = [_evaluate(rule, texts, deadline) for rule in rules]
    print(json.dumps(results, separators=(",", ":")))


if __name__ == "__main__":
    main()

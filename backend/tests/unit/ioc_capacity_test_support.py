"""Keep extraction capacity measurements independent of test instrumentation."""

import os


def untraced_child_environment() -> dict[str, str]:
    # pytest-cov 6 starts tracing child interpreters through their environment.
    # Capacity limits measure production runtime; parent coverage stays active.
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith("COV_CORE_") and key != "COVERAGE_PROCESS_START"
    }

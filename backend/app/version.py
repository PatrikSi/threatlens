from functools import lru_cache
import os
from pathlib import Path


_DEFAULT_VERSION = "1.8.0"


def _candidate_version_files() -> tuple[Path, ...]:
    app_dir = Path(__file__).resolve().parent
    return (
        app_dir.parents[1] / "VERSION",
        app_dir.parents[0] / "VERSION",
    )


@lru_cache
def get_app_version() -> str:
    env_version = os.getenv("APP_VERSION", "").strip()
    if env_version:
        return env_version

    for version_file in _candidate_version_files():
        try:
            version = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if version:
            return version

    return _DEFAULT_VERSION

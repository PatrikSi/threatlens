import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "backend" / "scripts" / "generate_api_reference.py"
OPENAPI_SCHEMA = REPO_ROOT / "docs" / "reference" / "openapi.json"


def test_api_reference_generator_uses_the_checked_in_source_version():
    environment = dict(os.environ)
    environment["APP_VERSION"] = "0.0.0-stale-runtime"

    subprocess.run(
        [sys.executable, str(GENERATOR)],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    expected_version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    schema = json.loads(OPENAPI_SCHEMA.read_text(encoding="utf-8"))
    assert schema["info"]["version"] == expected_version

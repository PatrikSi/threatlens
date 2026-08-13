import re
from pathlib import Path

from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[3]


def test_compose_forwards_every_backend_setting():
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    mapped_environment_names = set(re.findall(r"^  ([A-Z][A-Z0-9_]+):", compose_text, re.MULTILINE))
    settings_environment_names = {field_name.upper() for field_name in Settings.model_fields}

    assert settings_environment_names - mapped_environment_names - {"POSTGRES_PASSWORD", "REDIS_PASSWORD"} == set()

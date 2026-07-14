from app.version import get_app_version


def test_get_app_version_reads_repository_version(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    get_app_version.cache_clear()

    assert get_app_version() == "1.1.0"


def test_get_app_version_prefers_environment_override(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    get_app_version.cache_clear()

    try:
        assert get_app_version() == "1.2.3"
    finally:
        get_app_version.cache_clear()

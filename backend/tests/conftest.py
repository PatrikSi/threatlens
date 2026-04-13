from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
import uuid
import warnings
from pathlib import Path

import psycopg
import pytest
import redis as redis_lib
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.db.session as db_session_module
from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER
from app.core.security import get_password_hash
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.user import User
from app.services import auth_rate_limit
from app.tasks import feed_tasks

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_TEST_DATABASE_URL_ENV = "THREATLENS_TEST_DATABASE_URL"
_TEST_REDIS_URL_ENV = "THREATLENS_TEST_REDIS_URL"
_DOCKER_STARTUP_TIMEOUT_SECONDS = 60


@pytest.fixture(autouse=True)
def _stabilize_settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_FETCH", "false")
    monkeypatch.setenv("AI_ENABLED", "false")
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


class _AuthRateLimitRedis:
    def __init__(self):
        self.values: dict[str, int | str] = {}
        self.expirations: dict[str, float] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, expires_at in self.expirations.items() if expires_at <= now]
        for key in expired:
            self.values.pop(key, None)
            self.expirations.pop(key, None)

    def ttl(self, key: str):
        self._purge_expired()
        if key not in self.values:
            return -2
        expires_at = self.expirations.get(key)
        if expires_at is None:
            return -1
        remaining = expires_at - time.monotonic()
        if remaining <= 0:
            self.values.pop(key, None)
            self.expirations.pop(key, None)
            return -2
        return max(1, int(math.ceil(remaining)))

    def get(self, key: str):
        self._purge_expired()
        return self.values.get(key)

    def incr(self, key: str):
        self._purge_expired()
        current = int(self.values.get(key, 0)) + 1
        self.values[key] = current
        return current

    def decr(self, key: str):
        self._purge_expired()
        current = int(self.values.get(key, 0)) - 1
        self.values[key] = current
        return current

    def expire(self, key: str, seconds: int):
        self._purge_expired()
        if key not in self.values:
            return False
        self.expirations[key] = time.monotonic() + max(0, int(seconds))
        return True

    def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        self._purge_expired()
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = time.monotonic() + max(0, int(ex))
        else:
            self.expirations.pop(key, None)
        return True

    def delete(self, *keys: str):
        self._purge_expired()
        removed = 0
        for key in keys:
            existed = key in self.values or key in self.expirations
            self.values.pop(key, None)
            self.expirations.pop(key, None)
            if existed:
                removed += 1
        return removed

    def eval(self, script: str, numkeys: int, *args):
        self._purge_expired()
        _ = numkeys
        if "redis.call('get', KEYS[1]) == ARGV[1]" not in script:
            raise AssertionError("unexpected redis eval script")
        key = args[0]
        token = args[1]
        if self.get(key) == token:
            self.delete(key)
            return 1
        return 0


def _build_alembic_config(database_url: str) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _docker_is_available() -> bool:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    try:
        subprocess.run([docker_bin, "info"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def _docker_run(*args: str) -> str:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise RuntimeError("docker is not available")
    result = subprocess.run([docker_bin, *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _docker_remove(container_name: str) -> None:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return
    subprocess.run([docker_bin, "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _docker_mapped_port(container_name: str, container_port: str) -> int:
    port_output = _docker_run("port", container_name, container_port)
    return int(port_output.rsplit(":", 1)[1])


def _wait_for_postgres(database_url: str) -> None:
    connect_url = database_url.replace("+psycopg", "")
    deadline = time.monotonic() + _DOCKER_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(connect_url, connect_timeout=1) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("select 1")
                return
        except psycopg.Error:
            time.sleep(1)
    raise RuntimeError("timed out waiting for postgres test container")


def _wait_for_redis(redis_url: str) -> None:
    client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    deadline = time.monotonic() + _DOCKER_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            if client.ping():
                return
        except redis_lib.RedisError:
            time.sleep(1)
    raise RuntimeError("timed out waiting for redis test container")


@pytest.fixture(scope="session")
def test_database_url(tmp_path_factory: pytest.TempPathFactory):
    configured = os.getenv(_TEST_DATABASE_URL_ENV)
    if configured:
        yield configured
        return

    if _docker_is_available():
        container_name = f"threatlens-pytest-pg-{uuid.uuid4().hex[:8]}"
        try:
            try:
                _docker_run(
                    "run",
                    "-d",
                    "--rm",
                    "-P",
                    "--name",
                    container_name,
                    "-e",
                    "POSTGRES_DB=threatlens_test",
                    "-e",
                    "POSTGRES_USER=postgres",
                    "-e",
                    "POSTGRES_PASSWORD=postgres",
                    "postgres:16",
                )
                port = _docker_mapped_port(container_name, "5432/tcp")
                database_url = f"postgresql+psycopg://postgres:postgres@127.0.0.1:{port}/threatlens_test"
                _wait_for_postgres(database_url)
                yield database_url
                return
            except (RuntimeError, subprocess.CalledProcessError, psycopg.Error):
                warnings.warn("Falling back to SQLite because the Postgres test container could not start.", stacklevel=2)
        finally:
            _docker_remove(container_name)

    warnings.warn("Docker is unavailable; falling back to SQLite create_all() for tests.", stacklevel=2)
    database_path = tmp_path_factory.mktemp("db") / "threatlens-test.sqlite3"
    yield f"sqlite+pysqlite:///{database_path}"


@pytest.fixture(scope="session")
def test_redis_url():
    configured = os.getenv(_TEST_REDIS_URL_ENV)
    if configured:
        yield configured
        return

    if _docker_is_available():
        container_name = f"threatlens-pytest-redis-{uuid.uuid4().hex[:8]}"
        try:
            try:
                _docker_run(
                    "run",
                    "-d",
                    "--rm",
                    "-P",
                    "--name",
                    container_name,
                    "redis:7-alpine",
                    "redis-server",
                    "--appendonly",
                    "no",
                )
                port = _docker_mapped_port(container_name, "6379/tcp")
                redis_url = f"redis://127.0.0.1:{port}/0"
                _wait_for_redis(redis_url)
                yield redis_url
                return
            except (RuntimeError, subprocess.CalledProcessError, redis_lib.RedisError):
                warnings.warn("Falling back to the in-memory Redis test double because the Redis test container could not start.", stacklevel=2)
        finally:
            _docker_remove(container_name)

    yield None


@pytest.fixture(scope="session")
def database_engine(test_database_url: str):
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_database_url
    get_settings.cache_clear()

    connect_args = {"check_same_thread": False} if test_database_url.startswith("sqlite") else {}
    engine = create_engine(test_database_url, pool_pre_ping=True, connect_args=connect_args)

    if test_database_url.startswith("postgresql"):
        command.upgrade(_build_alembic_config(test_database_url), "head")
    else:
        Base.metadata.create_all(bind=engine)

    db_session_module.engine = engine
    db_session_module.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    feed_tasks.SessionLocal = db_session_module.SessionLocal
    try:
        yield engine
    finally:
        if test_database_url.startswith("sqlite"):
            Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()


@pytest.fixture()
def auth_rate_limit_backend(test_redis_url: str | None):
    if test_redis_url:
        backend = redis_lib.Redis.from_url(test_redis_url, decode_responses=True)
        backend.flushdb()
        try:
            yield backend
        finally:
            backend.flushdb()
        return

    yield _AuthRateLimitRedis()


@pytest.fixture()
def _install_test_redis_backend(monkeypatch: pytest.MonkeyPatch, auth_rate_limit_backend):
    monkeypatch.setattr(auth_rate_limit, "redis_client", auth_rate_limit_backend)
    monkeypatch.setattr(feed_tasks, "redis_client", auth_rate_limit_backend)


@pytest.fixture()
def db_session(database_engine) -> Session:
    connection = database_engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        class_=Session,
        join_transaction_mode="create_savepoint",
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session: Session, _install_test_redis_backend):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def seed_users(db_session: Session):
    admin = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash=get_password_hash("AdminPass123!"),
        role=ROLE_ADMIN,
        is_active=True,
    )
    analyst = User(
        id=uuid.uuid4(),
        email="analyst@example.com",
        password_hash=get_password_hash("AnalystPass123!"),
        role=ROLE_ANALYST,
        is_active=True,
    )
    viewer = User(
        id=uuid.uuid4(),
        email="viewer@example.com",
        password_hash=get_password_hash("ViewerPass123!"),
        role=ROLE_VIEWER,
        is_active=True,
    )
    db_session.add_all([admin, analyst, viewer])
    db_session.commit()
    return {"admin": admin, "analyst": analyst, "viewer": viewer}


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    payload = response.json()
    return payload["access_token"]


@pytest.fixture()
def auth_headers(client: TestClient, seed_users):
    return {
        "admin": {"Authorization": f"Bearer {_login(client, 'admin@example.com', 'AdminPass123!')}"},
        "analyst": {"Authorization": f"Bearer {_login(client, 'analyst@example.com', 'AnalystPass123!')}"},
        "viewer": {"Authorization": f"Bearer {_login(client, 'viewer@example.com', 'ViewerPass123!')}"},
    }

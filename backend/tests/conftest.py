import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER
from app.core.security import get_password_hash
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.user import User


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


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session: Session):
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

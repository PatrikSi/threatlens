#!/usr/bin/env python3
"""Seed or verify representative data around a release migration upgrade."""

from __future__ import annotations

import argparse
import json
import os
import uuid

from sqlalchemy import create_engine, text

FIXTURE_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000042")
FIXTURE_VIEW_ID = uuid.UUID("00000000-0000-4000-8000-000000000043")
FIXTURE_EMAIL = "migration-fixture@example.invalid"
FIXTURE_QUERY = {
    "filters": {"q": "migration-fixture", "read_status": "all"},
    "panel_rect": {"x": 12, "y": 18, "width": 640, "height": 420},
}


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL must be set")
    return value


def seed() -> None:
    engine = create_engine(_database_url())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, email, password_hash, password_login_enabled,
                    provisioning_source, role, is_active, is_approved,
                    auth_token_version
                ) VALUES (
                    :id, :email, :password_hash, true,
                    'local', 'viewer', true, true, 0
                )
                """
            ),
            {
                "id": FIXTURE_USER_ID,
                "email": FIXTURE_EMAIL,
                "password_hash": "migration-fixture-not-a-login-secret",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO saved_views (id, user_id, name, query_json)
                VALUES (:id, :user_id, :name, CAST(:query_json AS JSONB))
                """
            ),
            {
                "id": FIXTURE_VIEW_ID,
                "user_id": FIXTURE_USER_ID,
                "name": "Migration compatibility fixture",
                "query_json": json.dumps(FIXTURE_QUERY),
            },
        )
    engine.dispose()


def verify() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT users.email, saved_views.name, saved_views.query_json
                FROM users
                JOIN saved_views ON saved_views.user_id = users.id
                WHERE users.id = :user_id AND saved_views.id = :view_id
                """
            ),
            {"user_id": FIXTURE_USER_ID, "view_id": FIXTURE_VIEW_ID},
        ).one_or_none()
    engine.dispose()

    if row is None:
        raise RuntimeError("Migration compatibility fixture was not preserved")
    if row.email != FIXTURE_EMAIL or row.name != "Migration compatibility fixture":
        raise RuntimeError("Migration compatibility fixture identity changed")
    if row.query_json != FIXTURE_QUERY:
        raise RuntimeError("Migration compatibility fixture payload changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "verify"))
    args = parser.parse_args()
    if args.action == "seed":
        seed()
    else:
        verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

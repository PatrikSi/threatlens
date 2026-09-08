from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.services.classification import compute_classification_source_hash


def test_classification_revision_migration_reconciles_legacy_sources_and_downgrades(database_engine, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0086_classification_versions.py"
    spec = importlib.util.spec_from_file_location("classification_revision_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = f"classification_migration_{uuid.uuid4().hex}"
    with database_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(text("CREATE TABLE items (id bigint PRIMARY KEY, title text, summary text, first_seen_at timestamptz DEFAULT now())"))
        connection.execute(text("CREATE TABLE articles (item_id bigint PRIMARY KEY, text text, content_purged_at timestamptz)"))
        connection.execute(text("CREATE TABLE item_classifications (item_id bigint PRIMARY KEY, source_hash text)"))
        connection.execute(text("INSERT INTO items (id,title,summary) SELECT n, 'Café', 'Summary' FROM generate_series(1,6) n"))
        connection.execute(text("INSERT INTO articles(item_id,text,content_purged_at) VALUES (1,'Body',null),(2,'New body',null),(4,null,now()),(5,null,null)"))
        current_hash = compute_classification_source_hash(title="Café", summary="Summary", article_text="Body")
        absent_hash = compute_classification_source_hash(title="Café", summary="Summary", article_text=None)
        connection.execute(text("INSERT INTO item_classifications VALUES (1,:current),(2,:current),(4,:current),(5,:current),(6,:absent)"), {"current": current_hash, "absent": absent_hash})
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        assert connection.execute(text("SELECT classification_completed_version FROM items ORDER BY id")).scalars().all() == [1, 0, 0, 1, 0, 1]
        assert connection.execute(text("SELECT classification_required_version FROM items ORDER BY id")).scalars().all() == [1] * 6
        module.downgrade()
        assert connection.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_schema=:schema AND column_name LIKE 'classification_%'"), {"schema": schema}) == 0
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

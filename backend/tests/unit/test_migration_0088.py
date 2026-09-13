import importlib.util
from pathlib import Path
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_report_library_indexes_upgrade_search_and_downgrade(database_engine, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0088_report_library_indexes.py"
    spec = importlib.util.spec_from_file_location("report_library_indexes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = f"library_migration_{uuid.uuid4().hex}"
    with database_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        connection.execute(text("CREATE TABLE reports (id uuid PRIMARY KEY, title varchar(255), created_at timestamptz)"))
        connection.execute(text("CREATE TABLE ai_task_runs (created_at timestamptz, status text, error text)"))
        connection.execute(text("INSERT INTO reports VALUES (:id, 'Supply chain compromise', now())"), {"id": uuid.uuid4()})
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.upgrade()
        definitions = connection.execute(text("SELECT indexdef FROM pg_indexes WHERE schemaname=:schema"), {"schema": schema}).scalars().all()
        assert len(definitions) == 4
        assert any("USING gin" in definition and "simple" in definition for definition in definitions)
        assert any("WHERE" in definition and "error IS NOT NULL" in definition for definition in definitions)
        assert connection.scalar(text("SELECT count(*) FROM reports WHERE to_tsvector('simple', title) @@ websearch_to_tsquery('simple', '\"supply chain\"')")) == 1
        module.downgrade()
        assert connection.scalar(text("SELECT count(*) FROM pg_indexes WHERE schemaname=:schema"), {"schema": schema}) == 1
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

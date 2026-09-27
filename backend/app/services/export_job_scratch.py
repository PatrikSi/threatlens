"""Local scratch belongs to a database and claim; durable artifacts live in PostgreSQL."""
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import session as session_module
from app.models.export_job import ExportJob

PREFIX = "threatlens-export-job-"


def _scratch_root(db: Session) -> Path:
    # Use the session's actual bind, including endpoint, database, role and
    # connection options. Password rotation must not orphan the namespace.
    url = db.get_bind().engine.url
    identity = url._replace(drivername=url.get_backend_name(), password=None).difference_update_query(
        ["password", "sslpassword", "passfile"]
    )
    digest = hashlib.sha256(identity.render_as_string(hide_password=True).encode()).hexdigest()
    path = Path(tempfile.gettempdir()) / f"threatlens-export-scratch-{os.geteuid()}-{digest}"
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError("Export scratch namespace must be an owned private directory")
    return path


def export_scratch_directory(job_id: uuid.UUID, token: uuid.UUID) -> Path:
    with session_module.SessionLocal() as db:
        root = _scratch_root(db)
    path = root / f"{PREFIX}{job_id}-{token}"
    path.mkdir(mode=0o700)
    return path


def clean_local_export_scratch() -> None:
    # Run before accepting more rendering work on this host. A killed worker's
    # directory is removed once its claim has been cancelled or reconciled.
    with session_module.SessionLocal() as db:
        # Never infer ownership of legacy flat directories or other databases'
        # namespaces from an absent row in this database.
        root = _scratch_root(db)
        for path in root.glob(f"{PREFIX}*"):
            raw = path.name[len(PREFIX):]
            try:
                job_id, token = uuid.UUID(raw[:36]), uuid.UUID(raw[37:])
            except ValueError:
                continue
            if path.is_symlink() or not path.is_dir():
                continue
            job = db.get(ExportJob, job_id)
            if job is None or job.status != "running" or job.claim_token != token:
                shutil.rmtree(path, ignore_errors=True)

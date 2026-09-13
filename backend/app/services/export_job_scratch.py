"""Local scratch is claim-specific; durable artifacts live in PostgreSQL."""
import shutil
import tempfile
import uuid
from pathlib import Path

from app.db import session as session_module
from app.models.export_job import ExportJob

PREFIX = "threatlens-export-job-"


def export_scratch_directory(job_id, token):
    path = Path(tempfile.gettempdir()) / f"{PREFIX}{job_id}-{token}"
    path.mkdir(mode=0o700)
    return path


def clean_local_export_scratch():
    # Run before accepting more rendering work on this host. A killed worker's
    # directory is removed once its claim has been cancelled or reconciled.
    root = Path(tempfile.gettempdir())
    with session_module.SessionLocal() as db:
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

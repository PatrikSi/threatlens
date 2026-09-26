"""Independent recovery evidence. No application database or broker dependency."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timezone

KINDS = frozenset({"local_backup", "offhost_copy", "key_recovery", "host_loss_drill"})
DRILL_SCOPES = frozenset({"independent_target", "disposable_local_fault_domain_simulation"})
LABEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Evidence timestamps must be strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence timestamps require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def read_json(path: Path, limit: int = 1_048_576) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Operational artifacts must be regular files")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) > limit:
        raise ValueError("Operational artifact exceeds its size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Operational artifact must contain a JSON object")
    return value


def read_key(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in {0, os.geteuid()}:
            raise ValueError("Evidence key must be an owner-only regular file")
        value = os.read(descriptor, 4097).strip()
        if not 32 <= len(value) <= 4096:
            raise ValueError("Evidence key must contain 32–4096 bytes")
        return value
    finally:
        os.close(descriptor)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sign(value: dict, key: bytes) -> dict:
    body = {k: v for k, v in value.items() if k != "signature"}
    return {**body, "signature": hmac.new(key, canonical(body), hashlib.sha256).hexdigest()}


def verify(value: dict, key: bytes, *, deployment: str, kind: str, now: datetime) -> dict:
    signature = value.get("signature")
    expected = sign(value, key)["signature"]
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError("Evidence signature is invalid")
    if value.get("schema_version") != 1 or value.get("deployment") != deployment or value.get("kind") != kind:
        raise ValueError("Evidence identity does not match the policy")
    for name in ("issuer", "storage_domain"):
        if not isinstance(value.get(name), str) or not LABEL.fullmatch(value[name]):
            raise ValueError("Evidence requires bounded issuer and storage-domain identities")
    observed = timestamp(value["verified_at"])
    if (observed - now).total_seconds() > 300:
        raise ValueError("Evidence is dated in the future")
    if kind in {"local_backup", "offhost_copy", "host_loss_drill"}:
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("archive_sha256", ""))):
            raise ValueError("Evidence requires the verified archive digest")
        if type(value.get("archive_size_bytes")) is not int or value["archive_size_bytes"] <= 0:
            raise ValueError("Evidence requires a positive archive size")
        snapshot = timestamp(value["backup_snapshot_at"])
        if snapshot > observed:
            raise ValueError("Backup snapshot cannot be newer than its verification")
    if kind == "key_recovery" and value.get("decryption_verified") is not True:
        raise ValueError("Key evidence requires an independently verified decryption")
    if kind == "host_loss_drill" and not all(value.get(name) is True for name in (
        "source_environment_removed", "restore_verified", "outbound_quarantined", "independent_key_used",
    )):
        raise ValueError("Host-loss evidence requires reconstruction, restore and independent-key verification")
    if kind == "host_loss_drill":
        if value.get("qualification_scope") not in DRILL_SCOPES or type(value.get("production_qualified")) is not bool:
            raise ValueError("Host-loss evidence requires an explicit qualification scope")
        if value["production_qualified"] and value["qualification_scope"] != "independent_target":
            raise ValueError("A local simulation cannot qualify a production deployment")
        for name in ("backend_image_id", "backend_source_sha256", "migration_head"):
            if not isinstance(value.get(name), str) or not value[name] or len(value[name]) > 128:
                raise ValueError("Host-loss evidence requires its immutable application identity")
    return value


def check_recovery(policy: dict, *, deployment: str, now: datetime) -> tuple[dict, list[dict]]:
    metrics, incidents = {}, []
    for kind, objective in policy.get("evidence", {}).items():
        try:
            value = verify(read_json(Path(objective["receipt"])), read_key(Path(objective["verification_key"])),
                           deployment=deployment, kind=kind, now=now)
            if objective.get("issuer") and value["issuer"] != objective["issuer"]:
                raise ValueError("Unexpected evidence issuer")
            if kind in {"offhost_copy", "key_recovery", "host_loss_drill"}:
                if value["storage_domain"] == policy["primary_storage_domain"]:
                    raise ValueError("Recovery evidence is in the primary storage domain")
                if objective.get("storage_domain") != value["storage_domain"]:
                    raise ValueError("Recovery evidence is outside the explicitly expected storage domain")
            if kind == "host_loss_drill":
                expected_scope = objective.get("qualification_scope", "independent_target")
                if value["qualification_scope"] != expected_scope:
                    raise ValueError("Recovery drill does not qualify the required deployment scope")
                if expected_scope == "independent_target" and value["production_qualified"] is not True:
                    raise ValueError("Independent deployment qualification is incomplete")
                if objective.get("backend_source_sha256") and value["backend_source_sha256"] != objective["backend_source_sha256"]:
                    raise ValueError("Recovery drill used a different required source release")
            age_field = "backup_snapshot_at" if kind in {"local_backup", "offhost_copy"} else "verified_at"
            age = max(0, (now - timestamp(value[age_field])).total_seconds())
            metrics[kind] = {"available": True, "age_seconds": age, "max_age_seconds": objective["max_age_seconds"]}
            if age > objective["max_age_seconds"]:
                incidents.append({"scope": "recovery", "entity": kind, "code": "evidence_overdue"})
        except (OSError, ValueError, KeyError, TypeError):
            metrics[kind] = {"available": False, "age_seconds": None, "max_age_seconds": objective["max_age_seconds"]}
            incidents.append({"scope": "recovery", "entity": kind, "code": "evidence_missing_or_invalid"})
    return metrics, incidents

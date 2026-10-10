#!/usr/bin/env python3
"""Verify independent recovery artifacts and publish an authenticated receipt."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import secrets
from pathlib import Path
import sys

from evidence import KINDS, LABEL, canonical, read_json, read_key, sign, timestamp, verify
from monitor import atomic_json

ROOT = Path(__file__).resolve().parents[2]


def key_probe(key: bytes) -> dict:
    from cryptography.fernet import Fernet
    plain = secrets.token_bytes(32)
    cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))
    return {"schema_version": 1, "algorithm": "threatlens-fernet-sha256-v1",
            "ciphertext": cipher.encrypt(plain).decode(), "plaintext_sha256": hashlib.sha256(plain).hexdigest()}


def verify_key_probe(probe: dict, key: bytes) -> None:
    from cryptography.fernet import Fernet, InvalidToken
    if probe.get("schema_version") != 1 or probe.get("algorithm") != "threatlens-fernet-sha256-v1":
        raise ValueError("Unsupported key-recovery probe")
    try:
        plain = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest())).decrypt(probe["ciphertext"].encode())
    except (InvalidToken, KeyError, TypeError, AttributeError) as error:
        raise ValueError("Recovered key did not decrypt the independent probe") from error
    if hashlib.sha256(plain).hexdigest() != probe.get("plaintext_sha256"):
        raise ValueError("Recovered plaintext does not match the independent probe")


def verified_backup(path: Path) -> dict:
    # Reuse the recovery tool's regular-file/path protections, format checks,
    # archive-size verification and streaming SHA-256 implementation.
    sys.path.insert(0, str(ROOT / "scripts/recovery"))
    from recovery_manifest import RecoveryMetadataError, _verified_manifest
    try:
        return _verified_manifest(str(path))[2]
    except RecoveryMetadataError as error:
        raise ValueError("Backup manifest/archive verification failed") from error


def apply_drill_evidence(receipt: dict, result: dict, *, now: datetime) -> None:
    if (result.get("schema_version") != 1 or result.get("status") != "passed"
            or result.get("kind") != "host_loss_qualification" or result.get("deployment") != receipt["deployment"]
            or not isinstance(result.get("run_id"), str) or not LABEL.fullmatch(result["run_id"])
            or result.get("archive_sha256") != receipt["archive_sha256"]):
        raise ValueError("Drill evidence does not prove this archive was successfully reconstructed")
    completed = timestamp(result["finished_at"])
    if completed > now:
        raise ValueError("Drill completion is dated in the future")
    # Re-signing a historical artifact must never refresh its recovery age.
    receipt["verified_at"] = completed.isoformat()
    receipt["drill_result_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
    receipt["qualification_scope"] = result.get("scope")
    receipt["production_qualified"] = result.get("production_qualified")
    for name in ("backend_image_id", "backend_source_sha256", "migration_head"):
        receipt[name] = result.get(name)
    for name in ("source_environment_removed", "restore_verified", "outbound_quarantined", "independent_key_used"):
        receipt[name] = result.get(name) is True
    receipt["drill_run_id"] = result["run_id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=sorted(KINDS))
    parser.add_argument("--create-key-probe", action="store_true")
    parser.add_argument("--deployment")
    parser.add_argument("--issuer")
    parser.add_argument("--storage-domain")
    parser.add_argument("--signing-key", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--expected-archive-sha256")
    parser.add_argument("--recovered-key", type=Path)
    parser.add_argument("--key-probe", type=Path)
    parser.add_argument("--drill-result", type=Path)
    args = parser.parse_args()
    try:
        if args.create_key_probe:
            if args.recovered_key is None:
                parser.error("--create-key-probe requires --recovered-key")
            atomic_json(args.output, key_probe(read_key(args.recovered_key)))
            return 0
        if not args.kind or args.signing_key is None:
            parser.error("--kind and --signing-key are required when recording evidence")
        if any(not isinstance(value, str) or not LABEL.fullmatch(value) for value in (
            args.deployment, args.issuer, args.storage_domain,
        )):
            raise ValueError("Deployment, issuer and storage domain require bounded identities")
        now = datetime.now(timezone.utc)
        receipt = {"schema_version": 1, "deployment": args.deployment, "issuer": args.issuer,
                   "kind": args.kind, "storage_domain": args.storage_domain, "verified_at": now.isoformat()}
        if args.kind in {"local_backup", "offhost_copy", "host_loss_drill"}:
            if args.backup is None:
                parser.error("Archive evidence requires --backup")
            manifest = verified_backup(args.backup)
            receipt.update(archive_sha256=manifest["archive"]["sha256"],
                           archive_size_bytes=manifest["archive"]["size_bytes"],
                           backup_snapshot_at=manifest["snapshot_time_utc"])
            if args.kind != "local_backup" and args.expected_archive_sha256 != receipt["archive_sha256"]:
                raise ValueError("Independent copy/drill must match the separately supplied source archive digest")
        if args.kind in {"key_recovery", "host_loss_drill"}:
            if args.recovered_key is None or args.key_probe is None:
                parser.error("Key and host-loss evidence require --recovered-key and --key-probe")
            verify_key_probe(read_json(args.key_probe), read_key(args.recovered_key))
            receipt["decryption_verified"] = True
        if args.kind == "host_loss_drill":
            if args.drill_result is None:
                parser.error("Host-loss evidence requires --drill-result")
            apply_drill_evidence(receipt, read_json(args.drill_result), now=now)
        signed = sign(receipt, read_key(args.signing_key))
        verify(signed, read_key(args.signing_key), deployment=args.deployment, kind=args.kind, now=now)
        atomic_json(args.output, signed)
        print(f"Recorded verified {args.kind} evidence; no secret material was included.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Recovery evidence failed ({type(error).__name__}); inspect the configured evidence inputs.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

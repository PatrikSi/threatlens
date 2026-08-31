#!/usr/bin/env python3
"""Inspect or safely convert legacy OIDC-sourced IAM grants before migration 0065."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class LegacyGrantCounts:
    role_assignments: int
    group_memberships: int

    @property
    def total(self) -> int:
        return self.role_assignments + self.group_memberships


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL must be set")
    return value


def _managed_schema_installed(connection: Connection) -> bool:
    return bool(
        connection.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'iam_user_role_assignments' "
                "AND column_name = 'oidc_role_mapping_id')"
            )
        )
    )


def _legacy_counts(connection: Connection, *, lock: bool) -> LegacyGrantCounts:
    suffix = " FOR UPDATE" if lock else ""
    role_rows = connection.execute(
        text("SELECT id FROM iam_user_role_assignments WHERE source = 'oidc'" + suffix)
    ).all()
    group_rows = connection.execute(
        text("SELECT id FROM iam_group_memberships WHERE source = 'oidc'" + suffix)
    ).all()
    return LegacyGrantCounts(len(role_rows), len(group_rows))


def _convert_to_local(connection: Connection) -> tuple[int, int]:
    collapsed_roles = int(
        connection.execute(
            text(
                "DELETE FROM iam_user_role_assignments AS legacy "
                "USING iam_user_role_assignments AS keeper "
                "WHERE legacy.source = 'oidc' AND keeper.source = 'oidc' "
                "AND legacy.user_id = keeper.user_id "
                "AND legacy.role_id = keeper.role_id AND legacy.id > keeper.id"
            )
        ).rowcount
        or 0
    )
    collapsed_groups = int(
        connection.execute(
            text(
                "DELETE FROM iam_group_memberships AS legacy "
                "USING iam_group_memberships AS keeper "
                "WHERE legacy.source = 'oidc' AND keeper.source = 'oidc' "
                "AND legacy.user_id = keeper.user_id "
                "AND legacy.group_id = keeper.group_id AND legacy.id > keeper.id"
            )
        ).rowcount
        or 0
    )
    local_duplicate_roles = int(
        connection.execute(
            text(
                "DELETE FROM iam_user_role_assignments AS legacy "
                "USING iam_user_role_assignments AS local "
                "WHERE legacy.source = 'oidc' AND local.source = 'local' "
                "AND legacy.user_id = local.user_id "
                "AND legacy.role_id = local.role_id"
            )
        ).rowcount
        or 0
    )
    local_duplicate_groups = int(
        connection.execute(
            text(
                "DELETE FROM iam_group_memberships AS legacy "
                "USING iam_group_memberships AS local "
                "WHERE legacy.source = 'oidc' AND local.source = 'local' "
                "AND legacy.user_id = local.user_id "
                "AND legacy.group_id = local.group_id"
            )
        ).rowcount
        or 0
    )
    connection.execute(
        text(
            "UPDATE iam_user_role_assignments SET source = 'local', source_key = '' "
            "WHERE source = 'oidc'"
        )
    )
    connection.execute(
        text(
            "UPDATE iam_group_memberships SET source = 'local', source_key = '' "
            "WHERE source = 'oidc'"
        )
    )
    return (
        collapsed_roles + local_duplicate_roles,
        collapsed_groups + local_duplicate_groups,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect legacy OIDC IAM rows that migration 0065 cannot safely infer."
        )
    )
    parser.add_argument(
        "--convert-to-local",
        action="store_true",
        help="Preserve legacy access as locally managed assignments.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the conversion. Required with --convert-to-local.",
    )
    args = parser.parse_args()
    if args.convert_to_local and not args.yes:
        parser.error("--convert-to-local requires --yes")

    engine = create_engine(_database_url())
    try:
        with engine.begin() as connection:
            if _managed_schema_installed(connection):
                raise RuntimeError(
                    "Managed OIDC claim mappings are already installed. Use the "
                    "ThreatLens OIDC access-policy API or UI instead of this pre-upgrade tool."
                )
            counts = _legacy_counts(connection, lock=args.convert_to_local)
            print(
                "Legacy OIDC IAM grants: "
                f"role_assignments={counts.role_assignments} "
                f"group_memberships={counts.group_memberships}"
            )
            if counts.total == 0:
                print("OIDC access migration preflight passed.")
                return 0
            if not args.convert_to_local:
                print(
                    "Migration 0065 will stop before changing the schema. Review these "
                    "grants, then rerun with --convert-to-local --yes to preserve their "
                    "current access under local management.",
                    file=sys.stderr,
                )
                return 2
            duplicate_roles, duplicate_groups = _convert_to_local(connection)
            remaining = _legacy_counts(connection, lock=False)
            if remaining.total:
                raise RuntimeError(
                    "Legacy OIDC grants remained after conversion; the transaction was rolled back."
                )
            print(
                "Converted legacy grants to local management; removed duplicate local "
                f"rows: role_assignments={duplicate_roles} "
                f"group_memberships={duplicate_groups}."
            )
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

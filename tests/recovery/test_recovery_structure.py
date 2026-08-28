from __future__ import annotations

import re
import stat
import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECOVERY_DIRECTORY = REPOSITORY_ROOT / "scripts" / "recovery"


class RecoveryStructureTests(unittest.TestCase):
    def test_restore_library_is_not_executable_and_has_one_public_function(
        self,
    ) -> None:
        library = RECOVERY_DIRECTORY / "recovery_restore_lib.sh"
        mode = library.stat().st_mode
        self.assertFalse(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))

        function_names = re.findall(
            r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{",
            library.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        public_names = [name for name in function_names if not name.startswith("_")]
        self.assertEqual(public_names, ["tlr_restore_command", "tlr_reconcile_command"])

        recovery = (RECOVERY_DIRECTORY / "threatlens-recovery.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("_tlr_restore_emergency_rollback", recovery)
        self.assertIn("RESTORE_REPLACEMENT_ACTIVE", library.read_text(encoding="utf-8"))

    def test_destructive_restore_arms_rollback_before_the_first_fence(self) -> None:
        library = (RECOVERY_DIRECTORY / "recovery_restore_lib.sh").read_text(
            encoding="utf-8"
        )
        command = library[library.index("tlr_restore_command()") :]

        arm_position = command.index("_tlr_restore_arm_rollback")
        fence_position = command.index("_tlr_restore_create_clean_database")
        archive_position = command.index("_tlr_restore_archive")

        self.assertLess(arm_position, fence_position)
        self.assertLess(fence_position, archive_position)
        journal_position = command.index("_tlr_restore_journal_init")
        phase_position = command.index("_tlr_restore_set_phase fence_requested")
        self.assertLess(journal_position, phase_position)
        self.assertLess(phase_position, fence_position)
        self.assertIn("rollback_status", library)
        self.assertIn("return 2", library)

    def test_staged_inputs_are_revalidated_at_each_destructive_use(self) -> None:
        library = (RECOVERY_DIRECTORY / "recovery_restore_lib.sh").read_text(
            encoding="utf-8"
        )
        command = library[library.index("tlr_restore_command()") :]

        self.assertGreaterEqual(command.count("revalidate_staged_restore_inputs"), 4)
        self.assertLess(
            command.index("revalidate_staged_restore_inputs"),
            command.index('_tlr_restore_archive "${VERIFIED_ARCHIVE}"'),
        )

    def test_finalization_never_disables_both_database_login_roles(self) -> None:
        library = (RECOVERY_DIRECTORY / "recovery_restore_lib.sh").read_text(
            encoding="utf-8"
        )
        finalization = library[
            library.index("_tlr_restore_finalize_database()") : library.index(
                "_tlr_restore_run_isolated_preflight()"
            )
        ]

        restore_application_login = finalization.index(
            ":'app_role', CASE WHEN :'original_role_can_login'"
        )
        disable_recovery_login = finalization.index(
            "SELECT format('ALTER ROLE %I NOLOGIN', :'recovery_role')"
        )

        self.assertLess(restore_application_login, disable_recovery_login)
        self.assertIn(
            "_tlr_restore_set_phase application_access_restored", finalization
        )
        self.assertIn("_tlr_restore_set_phase replacement_verified", finalization)
        self.assertIn("_tlr_restore_set_phase forward_commit_requested", finalization)

    def test_clean_target_is_not_publicly_connectable_during_acl_setup(self) -> None:
        library = (RECOVERY_DIRECTORY / "recovery_restore_lib.sh").read_text(
            encoding="utf-8"
        )
        creation = library[
            library.index("_tlr_restore_create_clean_database()") : library.index(
                "_tlr_restore_rollback_database()"
            )
        ]

        create_disabled = creation.index("ALLOW_CONNECTIONS false")
        revoke_public = creation.index("REVOKE CONNECT ON DATABASE")
        grant_recovery = creation.index("GRANT CONNECT ON DATABASE")
        enable_target = creation.index("ALLOW_CONNECTIONS true")

        self.assertLess(create_disabled, revoke_public)
        self.assertLess(revoke_public, grant_recovery)
        self.assertLess(grant_recovery, enable_target)

    def test_original_identity_query_uses_psql_variables_not_identifier_interpolation(
        self,
    ) -> None:
        library = (RECOVERY_DIRECTORY / "recovery_restore_lib.sh").read_text(
            encoding="utf-8"
        )
        capture = library[
            library.index(
                "_tlr_restore_capture_original_access_state()"
            ) : library.index("_tlr_restore_journal_init()")
        ]

        self.assertIn('--set=db_name="$POSTGRES_DB"', capture)
        self.assertIn(":'db_name'", capture)
        self.assertNotIn("database.datname = '$POSTGRES_DB'", capture)

    def test_runbook_states_the_two_critical_recovery_distinctions(self) -> None:
        runbook = (REPOSITORY_ROOT / "docs" / "pages" / "operations.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Never copy, archive, or restore the Redis volume", runbook)
        self.assertIn(
            "Verification does not restore rows and is not a recovery drill", runbook
        )

    def test_emergency_rollback_allows_sanitized_failure_history_after_reconciliation(
        self,
    ) -> None:
        library = RECOVERY_DIRECTORY / "recovery_restore_lib.sh"
        harness = f"""
set -Eeuo pipefail
source {library!s}
RESTORE_REPLACEMENT_ACTIVE=true
RESTORE_ROLLBACK_DATABASE=rollback_database
OPERATION_LEDGER_ALLOWED=true
warn() {{ :; }}
_tlr_restore_rollback_database() {{
  RESTORE_REPLACEMENT_ACTIVE=false
  RESTORE_ROLLBACK_DATABASE=''
  return 0
}}
_tlr_restore_emergency_rollback
printf '%s|%s\n' "$RESTORE_REPLACEMENT_ACTIVE" "$OPERATION_LEDGER_ALLOWED"
"""

        result = subprocess.run(
            ["bash", "-c", harness],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "false|true")


if __name__ == "__main__":
    unittest.main()

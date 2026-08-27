from __future__ import annotations

import re
import stat
import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RECOVERY_DIRECTORY = REPOSITORY_ROOT / "scripts" / "recovery"


class RecoveryStructureTests(unittest.TestCase):
    def test_restore_library_is_not_executable_and_has_one_public_function(self) -> None:
        library = RECOVERY_DIRECTORY / "recovery_restore_lib.sh"
        mode = library.stat().st_mode
        self.assertFalse(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))

        function_names = re.findall(
            r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{",
            library.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        public_names = [name for name in function_names if not name.startswith("_")]
        self.assertEqual(public_names, ["tlr_restore_command"])

        recovery = (RECOVERY_DIRECTORY / "threatlens-recovery.sh").read_text(encoding="utf-8")
        self.assertIn("_tlr_restore_emergency_rollback", recovery)
        self.assertIn("RESTORE_REPLACEMENT_ACTIVE", library.read_text(encoding="utf-8"))

    def test_runbook_states_the_two_critical_recovery_distinctions(self) -> None:
        runbook = (REPOSITORY_ROOT / "docs" / "pages" / "operations.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Never copy, archive, or restore the Redis volume", runbook)
        self.assertIn("Verification does not restore rows and is not a recovery drill", runbook)

    def test_emergency_rollback_suppresses_history_in_the_original_database(self) -> None:
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
        self.assertEqual(result.stdout.strip(), "false|false")


if __name__ == "__main__":
    unittest.main()

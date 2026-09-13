from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DatabasePrivilegeBootstrapTests(unittest.TestCase):
    def test_generated_pasteable_mappings_keep_privileged_credentials_out_of_runtime(self) -> None:
        environment = dict(os.environ)
        for key in ("POSTGRES_USER", "POSTGRES_RUNTIME_USER", "POSTGRES_MIGRATION_USER"):
            environment.pop(key, None)
        generated = subprocess.run(
            [str(ROOT / "bootstrap.sh"), "--print-compose-env"],
            env=environment, check=True, capture_output=True, text=True,
        ).stdout
        sections = re.split(r"(?m)^x-([a-z-]+): &[^\n]+\n", generated)
        mappings = dict(zip(sections[1::2], sections[2::2], strict=True))
        database = dict(re.findall(r"(?m)^  ([A-Z_]+): '([^']*)'$", mappings["db-environment"]))
        runtime = mappings["backend-environment"]
        migration = mappings["migration-environment"]
        passwords = [database[key] for key in (
            "POSTGRES_PASSWORD", "POSTGRES_RUNTIME_PASSWORD", "POSTGRES_MIGRATION_PASSWORD",
        )]
        self.assertEqual(len(set(passwords)), 3)
        self.assertIn(database["POSTGRES_RUNTIME_PASSWORD"], runtime)
        self.assertNotIn(database["POSTGRES_PASSWORD"], runtime)
        self.assertNotIn(database["POSTGRES_MIGRATION_PASSWORD"], runtime)
        self.assertIn(database["POSTGRES_MIGRATION_PASSWORD"], migration)
        self.assertNotIn(database["POSTGRES_PASSWORD"], migration)
        self.assertNotIn(database["POSTGRES_RUNTIME_PASSWORD"], migration)
        self.assertNotIn("*db-environment", runtime)
        self.assertIn("RUN_MIGRATIONS_ON_STARTUP: 'false'", runtime)


if __name__ == "__main__":
    unittest.main()

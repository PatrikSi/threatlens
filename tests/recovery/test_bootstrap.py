from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class BootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="threatlens-bootstrap-test-")
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.work),
            "LANG": "C.UTF-8",
            "DOCKER_CONFIG": str(self.work / "docker-config"),
            "DOCKER_HOST": f"unix://{self.work}/unavailable-docker.sock",
            "TMPDIR": str(self.work),
            "ADMIN_EMAIL": "bootstrap-test@example.com",
            "ADMIN_PASSWORD": "bootstrap-test-administrator-password",
        }

    def run_bootstrap(self, *arguments: str, **overrides: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(ROOT / "bootstrap.sh"), *arguments],
            cwd=self.work,
            env=self.environment | overrides,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def require_compose(self) -> None:
        if shutil.which("docker") is None:
            self.skipTest("Docker Compose is required for interpolation checks")
        result = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, check=False, timeout=30
        )
        if result.returncode:
            self.skipTest("Docker Compose is required for interpolation checks")

    def compose(self, env_file: Path, compose_file: Path, *arguments: str) -> str:
        self.require_compose()
        environment = {
            key: value for key, value in self.environment.items()
            if key not in {"ADMIN_EMAIL", "ADMIN_PASSWORD"}
        }
        result = subprocess.run(
            [
                "docker", "compose", "--env-file", str(env_file),
                "--file", str(compose_file), "config", *arguments,
            ],
            cwd=self.work,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, "Compose configuration failed")
        return result.stdout

    def test_generated_file_includes_every_template_setting_comment_and_default(self) -> None:
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0)
        generated = (self.work / ".env").read_text()
        template = (ROOT / ".env.example").read_text()
        assignments = r"(?m)^([A-Z][A-Z0-9_]*)=(.*)$"
        generated_entries = re.findall(assignments, generated)
        template_entries = re.findall(assignments, template)
        actual = dict(generated_entries)
        expected = dict(template_entries)
        self.assertEqual(set(actual), set(expected))
        self.assertEqual(len(generated_entries), len(actual), "Generated settings must not be duplicated")
        self.assertEqual(len(template_entries), len(expected), "Template settings must not be duplicated")
        overrides = {
            "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
            "POSTGRES_RUNTIME_USER", "POSTGRES_RUNTIME_PASSWORD",
            "POSTGRES_MIGRATION_USER", "POSTGRES_MIGRATION_PASSWORD",
            "REDIS_PASSWORD", "JWT_SECRET", "APP_DATA_ENCRYPTION_KEY",
            "ADMIN_EMAIL", "ADMIN_PASSWORD", "APP_ENV", "AUTH_COOKIE_SECURE", "SEED_ADMIN_ON_STARTUP",
        }
        for key in expected.keys() - overrides:
            with self.subTest(setting=key):
                self.assertEqual(actual[key], expected[key])
        for line in template.splitlines():
            if line.startswith("#"):
                self.assertIn(line, generated.splitlines())
        for key in (
            "AI_ENABLED", "AI_API_KEY", "AI_API_KEY_BASE_URL", "AI_RESPONSE_MAX_BYTES",
            "ALLOW_PRIVATE_NETWORK_AI", "AI_AUTO_ENRICH_NEW_ITEM_MAX_AGE_HOURS",
            "AI_DAILY_BRIEF_SOURCE_AUDIT_LIMIT", "DISPATCH_AI_REPROCESS_BATCH_SIZE",
            "AI_TASK_HISTORY_RETENTION_DAYS", "AI_USAGE_RETENTION_DAYS", "AI_WORKER_CONCURRENCY",
        ):
            with self.subTest(ai_setting=key):
                self.assertIn(key, actual)
        self.assertEqual(actual["AI_ENABLED"], "false")
        self.assertEqual(actual["AI_API_KEY"], "")
        self.assertEqual(actual["APP_ENV"], "development")
        self.assertEqual(actual["AUTH_COOKIE_SECURE"], "false")
        self.assertEqual(actual["SEED_ADMIN_ON_STARTUP"], "true")

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is needed to verify the dependency-free ordinary mode")
    def test_ordinary_generation_requires_neither_python_nor_docker(self) -> None:
        binaries = self.work / "tools"
        binaries.mkdir()
        for name in ("bash", "openssl", "tr", "cut", "basename", "sed", "dirname", "mktemp", "cat", "ln", "rm"):
            executable = shutil.which(name)
            self.assertIsNotNone(executable, name)
            (binaries / name).symlink_to(executable)
        result = self.run_bootstrap(PATH=str(binaries))
        self.assertEqual(result.returncode, 0)
        self.assertIn("AI_RESPONSE_MAX_BYTES=", (self.work / ".env").read_text())

    def test_incomplete_checkout_fails_before_creating_credentials(self) -> None:
        checkout = self.work / "incomplete-checkout"
        checkout.mkdir()
        script = checkout / "bootstrap.sh"
        shutil.copy(ROOT / "bootstrap.sh", script)
        result = subprocess.run(
            [str(script)], cwd=self.work, env=self.environment,
            capture_output=True, text=True, check=False, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("complete matching ThreatLens checkout", result.stderr)
        self.assertFalse((self.work / ".env").exists())
        self.assertEqual(result.stdout, "")

    def test_dotenv_preserves_literal_credentials_and_trims_email(self) -> None:
        compose_file = self.work / "compose.yml"
        compose_file.write_text(
            'services:\n  probe:\n    image: unused\n    environment:\n'
            '      ADMIN_PASSWORD: "${ADMIN_PASSWORD}"\n'
        )
        passwords = [
            'dollar$UNSET ${ALSO_UNSET} #hash :colon',
            "single'quote\\backslash\\'mixed",
            'double"quote',
            "literal\\nsequence",
            "trailing\\",
            " leading and trailing whitespace ",
            "   ",
        ]
        for index, password in enumerate(passwords):
            with self.subTest(case=index):
                env_file = self.work / f"credentials-{index}.env"
                result = self.run_bootstrap(
                    str(env_file), ADMIN_PASSWORD=password,
                    ADMIN_EMAIL="  bootstrap-test@example.com  ",
                )
                self.assertEqual(result.returncode, 0)
                parsed = dict(
                    line.split("=", 1) for line in
                    self.compose(env_file, compose_file, "--environment").splitlines()
                    if "=" in line
                )
                self.assertEqual(parsed["ADMIN_PASSWORD"], password)
                self.assertEqual(parsed["ADMIN_EMAIL"], "bootstrap-test@example.com")

    def test_email_validation_preserves_plus_addressing_and_uppercase(self) -> None:
        for index, email in enumerate(("analyst+alerts@example.com", "SOC.Admin@Security.Example.COM")):
            with self.subTest(email=email):
                result = self.run_bootstrap(str(self.work / f"email-{index}.env"), ADMIN_EMAIL=email)
                self.assertEqual(result.returncode, 0)
                self.assertIn(f"Email:    {email}", result.stdout)

    def test_pasteable_credentials_survive_a_second_compose_interpolation(self) -> None:
        self.require_compose()
        password = "cash$UNSET ${OTHER_UNSET} \\ ' \" #literal"
        result = self.run_bootstrap(
            "--print-compose-env", ADMIN_PASSWORD=password,
            JWT_SECRET="ambient-jwt-must-not-override-generated-secret",
            DATABASE_URL="ambient-database-must-not-override-generated-connection",
        )
        self.assertEqual(result.returncode, 0)
        compose_file = self.work / "pasted-compose.yml"
        compose_file.write_text(
            result.stdout + '\nservices:\n  probe:\n    image: unused\n'
            '    environment:\n      <<: *backend-environment\n'
        )
        parsed = json.loads(self.compose(Path(os.devnull), compose_file, "--format", "json"))
        environment = parsed["services"]["probe"]["environment"]
        # config emits literal dollars doubled so its output remains pasteable.
        self.assertEqual(environment["ADMIN_PASSWORD"], password.replace("$", "$$"))
        self.assertNotIn("ambient-", environment["JWT_SECRET"])
        self.assertNotIn("ambient-", environment["DATABASE_URL"])
        self.assertEqual(environment["TRUSTED_PROXY_HOSTS"], "web")
        self.assertEqual(environment["BEAT_HEARTBEAT_TTL_SECONDS"], "360")
        self.assertEqual(list(self.work.glob("threatlens-bootstrap.*")), [])

    def test_custom_output_startup_commands_use_the_generated_file(self) -> None:
        destination = self.work / "custom settings.env"
        result = self.run_bootstrap(str(destination))
        self.assertEqual(result.returncode, 0)
        commands = [line.strip() for line in result.stdout.splitlines() if "docker compose" in line]
        self.assertEqual(
            [shlex.split(command) for command in commands],
            [
                ["docker", "compose", "--env-file", str(destination), "pull"],
                ["docker", "compose", "--env-file", str(destination), "up", "-d", "--wait"],
            ],
        )
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_existing_file_is_preserved_without_explicit_force(self) -> None:
        destination = self.work / ".env"
        destination.write_text("preserve-existing-credentials\n")
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(destination.read_text(), "preserve-existing-credentials\n")
        self.assertIn("Retain it for upgrades", result.stderr)

    def test_force_replaces_file_privately_without_leaving_scratch_files(self) -> None:
        destination = self.work / ".env"
        destination.write_text("old-file\n")
        destination.chmod(0o644)
        result = self.run_bootstrap("--force")
        self.assertEqual(result.returncode, 0)
        self.assertNotEqual(destination.read_text(), "old-file\n")
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(list(self.work.glob(".env.tmp.*")), [])

    def test_force_refuses_symlink_and_preserves_its_target(self) -> None:
        target = self.work / "important-file"
        target.write_text("must-remain-unchanged\n")
        (self.work / ".env").symlink_to(target)
        result = self.run_bootstrap("--force")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "must-remain-unchanged\n")
        self.assertTrue((self.work / ".env").is_symlink())

    def test_invalid_overrides_fail_before_creating_a_file(self) -> None:
        cases = [
            {"ADMIN_PASSWORD": "line\nbreak"},
            {"ADMIN_PASSWORD": "line\rbreak"},
            {"ADMIN_PASSWORD": "p" * 257},
            {"ADMIN_PASSWORD": "admin123"},
            {"ADMIN_PASSWORD": "replace-with-strong-admin-password"},
            {"ADMIN_EMAIL": "missing-address"},
            {"ADMIN_EMAIL": "admin @example.com"},
            {"ADMIN_EMAIL": "a..b@example.com"},
            {"ADMIN_EMAIL": ".admin@example.com"},
            {"ADMIN_EMAIL": "admin.@example.com"},
            {"ADMIN_EMAIL": "a@example..com"},
            {"ADMIN_EMAIL": "a@.example.com"},
            {"ADMIN_EMAIL": "a@example.com."},
            {"ADMIN_EMAIL": "a@-example.com"},
            {"ADMIN_EMAIL": "a@example-.com"},
            {"ADMIN_EMAIL": "a@under_score.example.com"},
            {"POSTGRES_RUNTIME_USER": "MixedCase"},
            {"POSTGRES_MIGRATION_USER": "pg_reserved"},
            {"POSTGRES_RUNTIME_USER": "threatlens_migration"},
            {"POSTGRES_USER": "threatlens_runtime"},
            {"POSTGRES_USER": "pg_reserved"},
            {"POSTGRES_USER": "x" * 64},
            {"POSTGRES_DB": "database/path"},
        ]
        for index, overrides in enumerate(cases):
            with self.subTest(case=index):
                result = self.run_bootstrap(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.work / ".env").exists())
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.strip())

    def test_unknown_option_is_not_created_as_an_output_file(self) -> None:
        result = self.run_bootstrap("--invalid")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.work / "--invalid").exists())

    def test_legacy_portainer_alias_still_renders_all_anchors(self) -> None:
        self.require_compose()
        result = self.run_bootstrap("--print-portainer-env")
        self.assertEqual(result.returncode, 0)
        for name in ("db", "redis", "migration", "backend"):
            self.assertIn(f"x-{name}-environment: &{name}-environment", result.stdout)

    def test_render_failure_cleans_private_scratch_and_redacts_diagnostics(self) -> None:
        binary_directory = self.work / "bin"
        binary_directory.mkdir()
        docker = binary_directory / "docker"
        docker.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, stat, sys\n"
            "path = pathlib.Path(sys.argv[3])\n"
            "if stat.S_IMODE(path.stat().st_mode) != 0o600:\n"
            "    raise SystemExit(2)\n"
            "print('sensitive-compose-diagnostic', file=sys.stderr)\n"
            "raise SystemExit(1)\n"
        )
        docker.chmod(0o755)
        result = self.run_bootstrap(
            "--print-compose-env", PATH=f"{binary_directory}:{self.environment['PATH']}"
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("sensitive-compose-diagnostic", result.stderr)
        self.assertNotIn(self.environment["ADMIN_PASSWORD"], result.stderr)
        self.assertIn("Docker Compose v2", result.stderr)
        self.assertEqual(list(self.work.glob("threatlens-bootstrap.*")), [])


if __name__ == "__main__":
    unittest.main()

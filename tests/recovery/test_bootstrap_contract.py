from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_BOUNDARY = "x-runtime-isolation:"
GENERATED_SECRETS = (
    ("db", "POSTGRES_PASSWORD"),
    ("db", "POSTGRES_RUNTIME_PASSWORD"),
    ("db", "POSTGRES_MIGRATION_PASSWORD"),
    ("redis", "REDIS_PASSWORD"),
    ("api", "JWT_SECRET"),
    ("api", "APP_DATA_ENCRYPTION_KEY"),
)


class BootstrapConfigurationContractTests(unittest.TestCase):
    """Compare deployment modes using Compose's actual interpolation rules.

    No containers or daemon connection are needed. This belongs with the
    standard-library recovery checks so drift is caught without building images
    or installing application dependencies.
    """

    @classmethod
    def setUpClass(cls) -> None:
        message = "Docker Compose is required to render bootstrap configuration"
        unavailable = AssertionError if os.environ.get("GITHUB_ACTIONS") == "true" else unittest.SkipTest
        if shutil.which("docker") is None:
            raise unavailable(message)
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise unavailable(message)

    def _run(
        self, arguments: list[str], *, cwd: Path, environment: dict[str, str]
    ) -> str:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        # Bootstrap and Compose output contain credentials. Report only the
        # command's name and status, never their captured output on failure.
        self.assertEqual(
            result.returncode,
            0,
            f"{Path(arguments[0]).name} exited with status {result.returncode}",
        )
        return result.stdout

    def _render(
        self,
        compose_file: Path,
        env_file: Path,
        *,
        cwd: Path,
        environment: dict[str, str],
    ) -> dict:
        return json.loads(
            self._run(
                [
                    "docker", "compose", "--env-file", str(env_file),
                    "--file", str(compose_file), "config", "--format", "json",
                ],
                cwd=cwd,
                environment=environment,
            )
        )

    def _normalized_environments(self, configuration: dict) -> dict:
        services = configuration["services"]
        replacements = []
        for service, key in GENERATED_SECRETS:
            value = services[service]["environment"].get(key)
            self.assertTrue(isinstance(value, str) and value, f"Missing {key}")
            replacements.append((value, f"<generated:{key}>"))

        environments = {}
        for service, settings in services.items():
            values = {}
            for key, value in settings.get("environment", {}).items():
                if isinstance(value, str):
                    for secret, replacement in replacements:
                        value = value.replace(secret, replacement)
                values[key] = value
            environments[service] = values
        return environments

    def test_pasteable_mappings_preserve_every_compose_environment_setting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="threatlens-bootstrap-contract-") as directory:
            work = Path(directory)
            environment = {
                "PATH": os.environ.get("PATH", os.defpath),
                "HOME": str(work),
                "DOCKER_CONFIG": str(work / "docker-config"),
                # Prevent bootstrap's existing-volume advisory from reaching
                # the developer's daemon. `compose config` remains offline.
                "DOCKER_HOST": f"unix://{work}/unavailable-docker.sock",
                "COMPOSE_PROJECT_NAME": "bootstrap-contract",
                "ADMIN_EMAIL": "bootstrap-contract@example.com",
                "ADMIN_PASSWORD": "bootstrap-contract-admin-credential",
            }
            generated_env = work / "generated.env"
            self._run(
                [str(ROOT / "bootstrap.sh"), str(generated_env)],
                cwd=work,
                environment=environment,
            )
            mappings = self._run(
                [str(ROOT / "bootstrap.sh"), "--print-compose-env"],
                cwd=work,
                environment=environment,
            )
            original = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
            self.assertEqual(original.count(ENVIRONMENT_BOUNDARY), 1)
            _, services = original.split(ENVIRONMENT_BOUNDARY, 1)
            pasted_compose = work / "pasteable-compose.yml"
            pasted_compose.write_text(
                f"{mappings}\n{ENVIRONMENT_BOUNDARY}{services}", encoding="utf-8"
            )
            pasted_compose.chmod(0o600)
            # Do not let the shell overrides mask a missing/incorrect value in
            # either generated output. Every value must come from that output.
            rendering_environment = {
                key: value for key, value in environment.items()
                if key not in {"ADMIN_EMAIL", "ADMIN_PASSWORD"}
            }
            ordinary = self._normalized_environments(self._render(
                ROOT / "docker-compose.yml", generated_env,
                cwd=work, environment=rendering_environment,
            ))
            pasteable = self._normalized_environments(self._render(
                pasted_compose, Path(os.devnull),
                cwd=work, environment=rendering_environment,
            ))

        self.assertEqual(set(ordinary), set(pasteable))
        for service in ordinary:
            with self.subTest(service=service):
                expected = ordinary[service]
                actual = pasteable[service]
                missing = sorted(set(expected) - set(actual))
                extra = sorted(set(actual) - set(expected))
                self.assertTrue(
                    not missing and not extra,
                    f"{service}: missing {len(missing)} settings {missing[:8]}; "
                    f"unexpected {len(extra)} settings {extra[:8]}",
                )
                changed = sorted(key for key in expected if expected[key] != actual[key])
                self.assertEqual(
                    changed, [],
                    f"{service}: pasteable values differ for {', '.join(changed)}",
                )


if __name__ == "__main__":
    unittest.main()

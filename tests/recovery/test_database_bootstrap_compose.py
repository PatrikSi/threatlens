from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


def database_wrapper() -> str:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"    entrypoint:\n      - bash\n      - -ceu\n      - \|\n(.*?)      - --\n", compose, re.S)
    if match is None:
        raise AssertionError("Database initialization wrapper is missing")
    return textwrap.dedent(match.group(1)).replace("$$", "$")


def embedded_script(wrapper: str) -> bytes:
    match = re.search(r"<<'THREATLENS_DATABASE_ROLES'\n(.*?)\nTHREATLENS_DATABASE_ROLES\n", wrapper, re.S)
    if match is None:
        raise AssertionError("Embedded database role script is missing")
    return base64.b64decode("".join(match.group(1).split()), validate=True)


class DatabaseBootstrapComposeTests(unittest.TestCase):
    def test_embedded_provisioning_is_exactly_the_canonical_script(self) -> None:
        self.assertEqual(
            embedded_script(database_wrapper()),
            (ROOT / "scripts/database/provision-roles.sh").read_bytes(),
        )
        subprocess.run(
            ["python3", str(ROOT / "scripts/database/sync-compose-init.py"), "--check"],
            check=True, capture_output=True, text=True,
        )

    def test_sync_updates_only_the_generated_block(self) -> None:
        synchronize = runpy.run_path(str(ROOT / "scripts/database/sync-compose-init.py"))["synchronized_compose"]
        source = b"#!/bin/bash\nprintf '%s\\n' \"${POSTGRES_DB}\"\n# SQL $$ and $validate$ remain literal.\n"
        fixture = (
            "unrelated: ${UNTOUCHED}\n"
            "        # BEGIN generated database initialization; do not edit\n"
            "        stale\n"
            "        # END generated database initialization\n"
            "other: unchanged\n"
        )
        updated = synchronize(fixture, source)
        self.assertTrue(updated.startswith("unrelated: ${UNTOUCHED}\n"))
        self.assertTrue(updated.endswith("other: unchanged\n"))
        block = updated.split("        # BEGIN", 1)[1].split("        # END", 1)[0]
        wrapper = textwrap.dedent(block.split("\n", 1)[1])
        self.assertEqual(embedded_script(wrapper), source)
        self.assertEqual(synchronize(updated, source), updated)
        with self.assertRaises(ValueError):
            synchronize(fixture + fixture, source)

    def test_wrapper_sources_nothing_and_preserves_stock_entrypoint_arguments(self) -> None:
        # No database environment or server is needed: the wrapper only stages
        # initialization. The official entrypoint owns the empty-volume decision.
        with tempfile.TemporaryDirectory(prefix="threatlens-db-wrapper-") as directory:
            staging = Path(directory)
            init_directory = staging / "init"
            init_directory.mkdir()
            entrypoint = staging / "stock-entrypoint"
            entrypoint.write_text(
                "#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            entrypoint.chmod(0o755)
            wrapper = database_wrapper().replace(
                "/docker-entrypoint-initdb.d", str(init_directory),
            ).replace("/usr/local/bin/docker-entrypoint.sh", str(entrypoint))
            subprocess.run(["bash", "-n"], input=wrapper, text=True, check=True)
            arguments = ["postgres", "-c", "application_name=bootstrap argument with spaces", "-c", "max_connections=40"]
            result = subprocess.run(
                ["bash", "-ceu", wrapper, "--", *arguments],
                env={"PATH": os.environ["PATH"]}, text=True, capture_output=True, check=True,
            )
            self.assertEqual(json.loads(result.stdout), arguments)
            script = init_directory / "10-threatlens-roles.sh"
            self.assertEqual(
                script.read_bytes(), b"(\n" + (ROOT / "scripts/database/provision-roles.sh").read_bytes() + b"\n)\n",
            )
            self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o444)
            # Sourcing the staged script must not leak its nounset option into
            # the stock entrypoint. A stub client avoids creating any database.
            client = staging / "psql"
            client.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            client.chmod(0o755)
            environment = {"PATH": str(staging) + os.pathsep + os.environ["PATH"]}
            environment.update(dict.fromkeys((
                "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_RUNTIME_USER", "POSTGRES_RUNTIME_PASSWORD",
                "POSTGRES_MIGRATION_USER", "POSTGRES_MIGRATION_PASSWORD",
            ), "bootstrap_fixture"))
            subprocess.run(
                ["bash", "-c", 'set +u; source "$1"; case "$-" in *u*) exit 42;; esac', "--", str(script)],
                env=environment, text=True, capture_output=True, check=True,
            )

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose is not installed")
    def test_pasted_configuration_has_no_host_file_dependencies(self) -> None:
        environment = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME"}}
        probe = subprocess.run(["docker", "compose", "version"], env=environment, capture_output=True)
        if probe.returncode:
            self.skipTest("Docker Compose is not installed")
        mappings = subprocess.run(
            [str(ROOT / "bootstrap.sh"), "--print-compose-env"],
            env=environment, check=True, capture_output=True, text=True,
        ).stdout
        source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="threatlens-paste-only-") as directory:
            compose = Path(directory) / "compose.yml"
            compose.write_text(mappings + "\n" + source[source.index("x-runtime-isolation:"):], encoding="utf-8")
            result = subprocess.run(
                ["docker", "compose", "--env-file", os.devnull, "--project-name", "bootstrap-config-test",
                 "--file", str(compose), "config", "--format", "json"],
                env=environment, text=True, capture_output=True, check=True,
            )
        document = json.loads(result.stdout)
        for service_name, service in document["services"].items():
            with self.subTest(service=service_name):
                self.assertFalse(any(volume["type"] == "bind" for volume in service.get("volumes", [])))
        database = document["services"]["db"]
        self.assertEqual(database["command"], ["postgres"])
        self.assertEqual(embedded_script(database["entrypoint"][2]), (ROOT / "scripts/database/provision-roles.sh").read_bytes())
        self.assertIn("/docker-entrypoint-initdb.d:rw,noexec,nosuid,size=1m,mode=0755", database["tmpfs"])
        self.assertIn("-h 127.0.0.1", database["healthcheck"]["test"][1])
        self.assertEqual(database["environment"]["POSTGRES_RUNTIME_USER"], "threatlens_runtime")
        self.assertEqual(document["services"]["migrate"]["depends_on"]["db"]["condition"], "service_healthy")


if __name__ == "__main__":
    unittest.main()

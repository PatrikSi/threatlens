import re
import shlex
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def workflow_steps():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text())
        for job in workflow.get("jobs", {}).values():
            yield from job.get("steps", [])


def test_external_actions_use_immutable_commits():
    actions = [step["uses"] for step in workflow_steps() if "uses" in step]
    assert actions
    for action in actions:
        if action.startswith("./"):
            continue
        _, separator, revision = action.partition("@")
        assert separator and re.fullmatch(r"[0-9a-f]{40}", revision), action


def test_every_image_scan_selects_a_safe_cli_and_preserves_failure_gates():
    scans = [
        step for step in workflow_steps()
        if step.get("uses", "").startswith("aquasecurity/trivy-action@")
    ]
    assert len(scans) >= 3
    for step in scans:
        options = step["with"]
        version = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", options.get("version", ""))
        assert version, "Select the CLI explicitly; the action's bundled default can be vulnerable."
        assert tuple(map(int, version.groups())) >= (0, 76, 2)
        assert str(options["exit-code"]) == "1"
        assert set(options["severity"].split(",")) == {"HIGH", "CRITICAL"}
        assert options["ignore-unfixed"] is True


def test_registry_attestations_do_not_require_optional_storage_permissions():
    attestations = [
        step for step in workflow_steps()
        if step.get("uses", "").startswith("actions/attest-build-provenance@")
    ]
    assert attestations
    for step in attestations:
        assert step["with"]["push-to-registry"] is True
        assert step["with"].get("create-storage-record") is False


def test_dependabot_covers_the_actual_docker_build_contexts_and_compose_files():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    directories = set()
    for update in config["updates"]:
        if update["package-ecosystem"] == "docker":
            directories.update(update.get("directories") or [update["directory"]])
    manifests = [*(ROOT / "docker").glob("*.Dockerfile"), *ROOT.glob("docker-compose*.yml")]
    assert manifests
    for manifest in manifests:
        parent = "/" + manifest.parent.relative_to(ROOT).as_posix().strip(".")
        assert parent in directories, f"Dependabot misses {manifest.relative_to(ROOT)}"


def test_backend_and_sdk_audit_the_installed_tooling_as_well_as_runtime_pins():
    workflow = yaml.safe_load((ROOT / ".github/workflows/quality-gates.yml").read_text())
    for name in ("backend", "mcp-sdk"):
        audits = []
        for step in workflow["jobs"][name]["steps"]:
            for line in step.get("run", "").replace("\\\n", " ").splitlines():
                command = shlex.split(line)
                if "pip_audit" in command:
                    audits.append(command)
        installed = [
            command for command in audits
            if not any(flag in command for flag in ("-r", "--requirement", "--path"))
        ]
        assert installed, f"{name} must include installed pip, pytest and SDK dependencies"
        assert all("--strict" in command and "--ignore-vuln" not in command for command in installed)

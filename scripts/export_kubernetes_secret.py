#!/usr/bin/env python3
"""Export one service's resolved environment as a Kubernetes Secret, without applying it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from bootstrap_compose_env import load_configuration


ROOT = Path(__file__).resolve().parents[1]
DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
SECRET_KEY = re.compile(r"[A-Za-z0-9._-]+")


def valid_name(value: str, *, namespace: bool = False) -> bool:
    labels = value.split(".")
    return (
        len(value) <= (63 if namespace else 253)
        and (not namespace or len(labels) == 1)
        and all(len(label) <= 63 and DNS_LABEL.fullmatch(label) for label in labels)
    )


def export_secret(configuration: dict, *, service: str, name: str, namespace: str | None) -> dict:
    if not valid_name(name) or (namespace is not None and not valid_name(namespace, namespace=True)):
        raise ValueError("Invalid Kubernetes Secret name or namespace")
    services = configuration.get("services")
    if not isinstance(services, dict) or not isinstance(services.get(service), dict):
        raise ValueError("The selected service is not in the Compose configuration")
    settings = services[service].get("environment", {})
    if not isinstance(settings, dict) or not all(
        isinstance(key, str) and SECRET_KEY.fullmatch(key) and isinstance(value, str)
        for key, value in settings.items()
    ):
        raise ValueError("Service environment contains an unresolved value")
    # Compose config escapes literal dollars for another Compose parse. Secret
    # values are already resolved data; Kubernetes must receive the originals.
    values = {key: value.replace("$$", "$") for key, value in settings.items()}
    metadata = {"name": name}
    if namespace is not None:
        metadata["namespace"] = namespace
    secret = {
        "apiVersion": "v1", "kind": "Secret", "metadata": metadata,
        "type": "Opaque", "stringData": values,
    }
    if len(json.dumps(secret).encode("utf-8")) > 1_048_576:
        raise ValueError("Service environment exceeds the Secret size budget")
    return secret


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True, help="Existing Compose dotenv file; never regenerated")
    parser.add_argument("--compose-file", type=Path, default=ROOT / "docker-compose.yml")
    parser.add_argument("--service", required=True, help="Compose service, such as api, worker-ai, or migrate")
    parser.add_argument("--name", required=True, help="Kubernetes Secret name")
    parser.add_argument("--namespace", help="Namespace shared by the Secret and its consuming Pods")
    args = parser.parse_args()
    if not valid_name(args.name) or (args.namespace is not None and not valid_name(args.namespace, namespace=True)):
        parser.error("Use a valid DNS subdomain for --name and a DNS label for --namespace")
    try:
        configuration = load_configuration(args.env_file.resolve(), args.compose_file.resolve())
        secret = export_secret(configuration, service=args.service, name=args.name, namespace=args.namespace)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        # Compose errors can contain interpolated credentials. Never copy them
        # or partially rendered secrets into failure output.
        print(
            "Unable to export the service environment. Check the existing --env-file, "
            "--compose-file, service name, and Docker Compose v2 installation. "
            "The input file was not changed.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(secret, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

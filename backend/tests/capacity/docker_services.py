"""Disposable services whose destructive operations require their own stored ID."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

import psycopg
import redis


class DockerService:
    def __init__(self, kind):
        self.kind = kind
        self.id = None
        self.run_id = os.environ["THREATLENS_CAPACITY_RUN_ID"]
        self.limits = json.loads(os.environ.get("THREATLENS_CAPACITY_LIMITS", "{}"))
        self.name = f"threatlens-capacity-{kind}-{uuid.uuid4().hex[:12]}"

    def command(self, *args):
        return subprocess.check_output(
            ["docker", *args], text=True, stderr=subprocess.STDOUT, timeout=90
        ).strip()

    def __enter__(self):
        memory = self.limits.get(
            f"{self.kind}_memory_mib", 512 if self.kind == "postgres" else 128
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            host_port = reservation.getsockname()[1]
        service_port = 5432 if self.kind == "postgres" else 6379
        args = [
            "run",
            "-d",
            "-p",
            f"127.0.0.1:{host_port}:{service_port}",
            "--name",
            self.name,
            "--cpus",
            str(self.limits.get("container_cpus", 0.5)),
            "--memory",
            f"{memory}m",
            "--memory-swap",
            f"{memory}m",
            "--label",
            f"threatlens.capacity.run_id={self.run_id}",
        ]
        if self.kind == "postgres":
            args += [
                "-e",
                "POSTGRES_DB=threatlens_test",
                "-e",
                "POSTGRES_USER=postgres",
                "-e",
                "POSTGRES_PASSWORD=postgres",
                os.environ.get("THREATLENS_TEST_POSTGRES_IMAGE", "postgres:16"),
            ]
            port = "5432/tcp"
        else:
            args += [
                os.environ.get("THREATLENS_TEST_REDIS_IMAGE", "redis:7-alpine"),
                "redis-server",
                "--appendonly",
                "yes",
                "--appendfsync",
                "always",
            ]
            port = "6379/tcp"
        try:
            self.id = self.command(*args)
            manifest = Path(os.environ["THREATLENS_CAPACITY_CONTAINER_MANIFEST"])
            with manifest.open("a") as target:
                target.write(self.id + "\n")
            binding = self.command("port", self.id, port).splitlines()[0]
            mapped = int(binding.rsplit(":", 1)[1])
            self.url = (
                f"postgresql+psycopg://postgres:postgres@127.0.0.1:{mapped}/threatlens_test"
                if self.kind == "postgres"
                else f"redis://127.0.0.1:{mapped}/0"
            )
            self.wait_ready()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def wait_ready(self):
        until = time.monotonic() + 60
        while time.monotonic() < until:
            try:
                if self.kind == "postgres":
                    with psycopg.connect(
                        self.url.replace("postgresql+psycopg://", "postgresql://"),
                        connect_timeout=1,
                    ) as connection:
                        connection.execute("SELECT 1")
                else:
                    with redis.Redis.from_url(
                        self.url, socket_timeout=1, socket_connect_timeout=1
                    ) as connection:
                        connection.ping()
                return
            except (psycopg.Error, redis.RedisError):
                time.sleep(0.2)
        raise TimeoutError(f"owned {self.kind} service did not become ready")

    def crash(self):
        assert self.id
        self.command("kill", "--signal", "KILL", self.id)

    def restart(self):
        assert self.id
        self.command("start", self.id)
        self.wait_ready()

    def __exit__(self, *_args):
        if self.id:
            subprocess.run(
                ["docker", "rm", "-f", self.id], capture_output=True, timeout=20
            )

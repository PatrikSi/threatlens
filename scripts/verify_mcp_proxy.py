#!/usr/bin/env python3
"""Qualify MCP through bundled nginx and Uvicorn using disposable synthetic data.

Uses existing local dependency images with current application and nginx source
mounted read-only; this does not rebuild release images. Never reads .env or
connects to a running stack.
The only published port is random and bound to 127.0.0.1. All containers and the
networks are removed on exit. Data services use an internal network; nginx also
joins a separate ingress network. Optionally qualify the official SDK using
an interpreter with mcp==2.2.0 and httpx2 installed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid


LATEST = "2026-07-28"
LEGACY = "2025-11-25"
SEED = """
from datetime import datetime, timedelta, timezone
import json
from app.core.security import generate_api_token
from app.db.session import SessionLocal
from app.models.api_token import ApiToken
from app.models.article import Article
from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
from app.models.feed import Feed
from app.models.item import Item
from app.models.user import User
with SessionLocal() as db:
    user = User(email='proxy-smoke@example.test', password_hash='!', role='viewer', is_active=True, is_approved=True)
    feed = Feed(name='Proxy smoke source', url='https://feed.example.test/synthetic.xml', enabled=False, handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID)
    db.add_all([user, feed]); db.flush()
    item = Item(feed_id=feed.id, url='https://article.example.test/synthetic', title='Synthetic proxy evidence', summary='Stored defensive evidence.', dedupe_key='mcp-proxy-smoke', content_hash='a'*64)
    db.add(item); db.flush()
    db.add(Article(item_id=item.id, final_url=item.url, http_status=200, text='Synthetic stored evidence through nginx.', extraction_method='fixture'))
    value, prefix, digest = generate_api_token()
    token = ApiToken(user_id=user.id, name='Proxy qualification', token_prefix=prefix, token_hash=digest, scopes=['read:mcp', 'read:items'], expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    db.add(token); db.commit()
    print(json.dumps({'token':value, 'token_id':str(token.id), 'item_id':str(item.id)}))
"""
SDK = """
import asyncio, json, sys
from importlib.metadata import version
import mcp, httpx2
from mcp.client.streamable_http import streamable_http_client
assert version('mcp') == '2.2.0'
values = json.load(sys.stdin)
async def exercise():
    for mode, expected in [('auto','2026-07-28'), ('legacy','2025-11-25')]:
        async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+values['token']}, trust_env=False) as http:
            async with mcp.Client(streamable_http_client(values['url'], http_client=http), mode=mode, read_timeout_seconds=10) as client:
                assert client.protocol_version == expected
                tools = await client.list_tools()
                assert 'search_articles' in {entry.name for entry in tools.tools}
                result = await client.call_tool('get_article_evidence', {'item_id':values['item_id']})
                assert not result.is_error
                assert result.structured_content['data']['article_text'] == 'Synthetic stored evidence through nginx.'
                print('Official SDK 2.2.0 real proxy: '+mode+' passed')
asyncio.run(exercise())
"""


def run(*arguments: str, timeout: int = 60, input_text: str | None = None) -> str:
    result = subprocess.run(
        arguments, text=True, input=input_text, capture_output=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(
            f"{arguments[0]} failed ({result.returncode}): {result.stderr[-3000:]}"
        )
    return result.stdout.strip()


def wait_until(check, *, seconds: int = 45) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (RuntimeError, HTTPError, URLError, TimeoutError):
            pass
        time.sleep(0.3)
    raise RuntimeError(
        "Disposable service did not become ready within its startup budget"
    )


def rpc(
    url,
    token,
    method="tools/list",
    *,
    version=LATEST,
    name=None,
    arguments=None,
    origin=None,
    params=None,
):
    parameters = dict(params or {})
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "MCP-Protocol-Version": version,
        "Authorization": "Bearer " + token,
        "X-Request-ID": "mcp-proxy-" + uuid.uuid4().hex,
    }
    if version == LATEST:
        parameters["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        headers["MCP-Method"] = method
    if name is not None:
        parameters["name"] = name
        headers["MCP-Name"] = name
    if arguments is not None:
        parameters["arguments"] = arguments
    if origin:
        headers["Origin"] = origin
    body = json.dumps(
        {"jsonrpc": "2.0", "id": "proxy-smoke", "method": method, "params": parameters}
    ).encode()
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        response = urlopen(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        wire = response.read()
        return response.status, response.headers, json.loads(wire), len(wire)


def qualify(url, values):
    for version in (LATEST, LEGACY):
        method = "server/discover" if version == LATEST else "initialize"
        params = (
            {}
            if version == LATEST
            else {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "proxy-smoke", "version": "1"},
            }
        )
        status, headers, body, _ = rpc(
            url, values["token"], method, version=version, params=params
        )
        assert status == 200 and "result" in body, (status, body)
        assert headers["MCP-Protocol-Version"] == version
        status, headers, body, _ = rpc(
            url, values["token"], version=version, origin="https://client.example"
        )
        assert status == 200, (status, body)
        assert {tool["name"] for tool in body["result"]["tools"]} == {
            "search_articles",
            "get_article_evidence",
        }
        assert headers["Access-Control-Allow-Origin"] == "https://client.example"
        assert headers.get("X-Request-ID") and "no-store" in headers["Cache-Control"]
        assert headers.get("MCP-Session-ID") is None
        status, _, body, size = rpc(
            url,
            values["token"],
            "tools/call",
            version=version,
            name="search_articles",
            arguments={"q": "Synthetic proxy"},
        )
        assert status == 200 and not body["result"]["isError"], (status, body)
        assert (
            body["result"]["structuredContent"]["data"]["articles"][0]["item_id"]
            == values["item_id"]
        )
        assert size <= 65536
        status, _, body, _ = rpc(
            url,
            values["token"],
            "tools/call",
            version=version,
            name="get_article_evidence",
            arguments={"item_id": values["item_id"]},
        )
        assert status == 200 and not body["result"]["isError"], (status, body)
        assert (
            body["result"]["structuredContent"]["data"]["article_text"]
            == "Synthetic stored evidence through nginx."
        )
        print(
            f"Real nginx/Uvicorn {version}: discovery, scoped catalogue, search and evidence passed",
            flush=True,
        )
    status, _, body, _ = rpc(url, values["token"], origin="https://denied.example")
    assert status == 403 and body["error"]["data"]["code"] == "mcp_origin_denied"
    print("Origin denial and allowed-origin CORS passed", flush=True)


def verify(args):
    root = Path(__file__).resolve().parents[1]
    suffix = uuid.uuid4().hex[:12]
    network = f"threatlens-mcp-proxy-{suffix}"
    ingress_network = network + "-ingress"
    containers = [
        f"{network}-{role}"
        for role in ("db", "redis", "migration", "seed", "api", "web")
    ]
    db_name, redis_name, migration_name, seed_name, api_name, web_name = containers
    created_networks = []
    images = (args.backend_image, args.web_image, args.postgres_image, args.redis_image)
    for image in images:
        run("docker", "image", "inspect", image, "--format", "{{.Id}}")
    with tempfile.TemporaryDirectory(prefix="threatlens-mcp-proxy-") as temporary:
        scratch = Path(temporary)
        empty_env = scratch / "empty.env"
        empty_env.write_text("")
        proxy_conf = scratch / "default.conf"
        proxy_conf.write_text(
            (root / "web/nginx/default.conf.template")
            .read_text()
            .replace("${THREATLENS_CSP_CONNECT_SRC}", "'self'")
            .replace("${THREATLENS_CSP_FRAME_SRC}", "'self'")
        )
        password = secrets.token_hex(20)
        environment = {
            "APP_ENV": "development",
            "DATABASE_URL": f"postgresql+psycopg://mcp_smoke:{password}@db:5432/mcp_smoke",
            "REDIS_URL": "redis://redis:6379/0",
            "MCP_ENABLED": "true",
            "MCP_ALLOWED_ORIGINS": "https://client.example",
            "MCP_RATE_LIMIT_PER_MINUTE": "1000",
            "AI_ENABLED": "false",
            "AI_API_KEY": "",
            "JWT_SECRET": secrets.token_hex(32),
            "APP_DATA_ENCRYPTION_KEY": "",
            "REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY": "false",
            "PUBLIC_APP_URL": "https://threatlens.example",
            "SEED_ADMIN_ON_STARTUP": "false",
            "RUN_MIGRATIONS_ON_STARTUP": "false",
            "DATABASE_POOL_SIZE": "6",
            "DATABASE_MAX_OVERFLOW": "0",
        }
        backend_options = [
            "--network",
            network,
            "-v",
            f"{root / 'backend/app'}:/app/app:ro",
            "-v",
            f"{root / 'backend/alembic'}:/app/alembic:ro",
            "-v",
            f"{root / 'backend/alembic.ini'}:/app/alembic.ini:ro",
            "-v",
            f"{empty_env}:/app/.env:ro",
        ]
        for key, value in environment.items():
            backend_options.extend(["-e", f"{key}={value}"])
        try:
            run("docker", "network", "create", "--internal", network)
            created_networks.append(network)
            run("docker", "network", "create", ingress_network)
            created_networks.append(ingress_network)
            run(
                "docker",
                "run",
                "-d",
                "--name",
                db_name,
                "--network",
                network,
                "--network-alias",
                "db",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=256m",
                "-e",
                "POSTGRES_USER=mcp_smoke",
                "-e",
                f"POSTGRES_PASSWORD={password}",
                "-e",
                "POSTGRES_DB=mcp_smoke",
                args.postgres_image,
            )
            run(
                "docker",
                "run",
                "-d",
                "--name",
                redis_name,
                "--network",
                network,
                "--network-alias",
                "redis",
                args.redis_image,
                "redis-server",
                "--save",
                "",
                "--appendonly",
                "no",
            )
            wait_until(
                lambda: run(
                    "docker",
                    "exec",
                    db_name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "mcp_smoke",
                    "-d",
                    "mcp_smoke",
                ).endswith("accepting connections")
            )
            wait_until(
                lambda: run("docker", "exec", redis_name, "redis-cli", "ping") == "PONG"
            )
            print(
                "Disposable PostgreSQL/Redis ready; applying current migrations",
                flush=True,
            )
            run(
                "docker",
                "run",
                "--rm",
                "--name",
                migration_name,
                *backend_options,
                args.backend_image,
                "alembic",
                "upgrade",
                "head",
                timeout=180,
            )
            values = json.loads(
                run(
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    seed_name,
                    *backend_options,
                    args.backend_image,
                    "python",
                    "-c",
                    SEED,
                )
            )
            run(
                "docker",
                "run",
                "-d",
                "--name",
                api_name,
                *backend_options,
                "--network-alias",
                "api",
                args.backend_image,
                "python",
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--no-access-log",
            )
            wait_until(
                lambda: (
                    run(
                        "docker",
                        "exec",
                        api_name,
                        "python",
                        "-c",
                        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/openapi.json',timeout=2).status)",
                    )
                    == "200"
                )
            )
            run(
                "docker",
                "run",
                "-d",
                "--name",
                web_name,
                "--network",
                f"name={ingress_network},gw-priority=1",
                "--network",
                network,
                "-p",
                "127.0.0.1::3000",
                "--tmpfs",
                "/tmp:rw,size=16m",
                "-v",
                f"{root / 'web/nginx/nginx.conf'}:/etc/nginx/nginx.conf:ro",
                "-v",
                f"{proxy_conf}:/etc/nginx/conf.d/default.conf:ro",
                "--entrypoint",
                "nginx",
                args.web_image,
                "-g",
                "daemon off;",
            )
            port = run("docker", "port", web_name, "3000/tcp").rsplit(":", 1)[1]
            url = f"http://127.0.0.1:{port}/api/v1/mcp"
            wait_until(
                lambda: urlopen(f"http://127.0.0.1:{port}/", timeout=2).status == 200
            )
            qualify(url, values)
            if args.sdk_python:
                print(
                    run(
                        args.sdk_python,
                        "-c",
                        SDK,
                        input_text=json.dumps({**values, "url": url}),
                        timeout=90,
                    ),
                    flush=True,
                )
            else:
                print(
                    "Official SDK network check skipped: supply --sdk-python with mcp==2.2.0",
                    flush=True,
                )
            revoke = "from datetime import datetime,timezone; import uuid; from app.db.session import SessionLocal; from app.models.api_token import ApiToken; db=SessionLocal(); token=db.get(ApiToken,uuid.UUID(__import__('sys').argv[1])); assert token.last_used_at is not None; token.revoked_at=datetime.now(timezone.utc); db.commit(); db.close()"
            run("docker", "exec", api_name, "python", "-c", revoke, values["token_id"])
            status, _, body, _ = rpc(url, values["token"])
            assert status in {401, 403} and "error" in body, (status, body)
            print(
                "Credential-use commit and subsequent token revocation denial passed",
                flush=True,
            )
            print("MCP real proxy qualification passed", flush=True)
        finally:
            for container in reversed(containers):
                subprocess.run(
                    ["docker", "rm", "-f", container],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
            for created_network in reversed(created_networks):
                subprocess.run(
                    ["docker", "network", "rm", created_network],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
            print("Disposable MCP proxy resources removed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", default="threatlens-backend:dev")
    parser.add_argument("--web-image", default="threatlens-web:dev")
    parser.add_argument("--postgres-image", default="postgres:16")
    parser.add_argument("--redis-image", default="redis:7-alpine")
    parser.add_argument("--sdk-python")
    verify(parser.parse_args())

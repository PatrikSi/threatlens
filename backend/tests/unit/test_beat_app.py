"""Beat publication contracts without executing worker implementations."""

import ast
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

from celery import Celery
from celery.beat import ScheduleEntry
from kombu import Producer
from kombu.utils.imports import symbol_by_name
import pytest

from app.core.config import get_settings
from app.core.worker_queues import QUEUE_AI, QUEUE_AI_REPORTS, QUEUE_AI_REPORTS_EDITORIAL
from app.tasks import beat_scheduler, beat_watchdog, bounded_beat
from app.tasks.beat_app import BeatCelery, beat_app
from app.tasks.celery_app import celery_app as worker_app


_BACKEND = Path(__file__).resolve().parents[2]
_NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
_PUBLICATION_ATTRIBUTES = {
    "queue", "routing_key", "exchange", "priority", "expires", "serializer",
    "delivery_mode", "compression", "time_limit", "soft_time_limit", "immediate",
    "mandatory", "base", "shadow_name",
}


def _isolated_environment(*, ai_enabled=False):
    environment = dict(os.environ)
    environment.update(
        APP_ENV="development",
        JWT_SECRET="",
        APP_DATA_ENCRYPTION_KEY="",
        APP_DATA_ENCRYPTION_PREVIOUS_KEYS="",
        REQUIRE_EXPLICIT_DATA_ENCRYPTION_KEY="false",
        AI_ENABLED=str(ai_enabled).lower(),
        AI_API_KEY="",
        PYTHONPATH=str(_BACKEND),
        PYTHONDONTWRITEBYTECODE="1",
    )
    return environment


@lru_cache
def _periodic_declarations():
    declarations = {}
    # Several compatibility task names live in split implementation modules.
    for path in (_BACKEND / "app/tasks").glob("*.py"):
        for function in ast.parse(path.read_text()).body:
            if not isinstance(function, ast.FunctionDef):
                continue
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                named = {keyword.arg: keyword.value for keyword in decorator.keywords}
                if isinstance(named.get("name"), ast.Constant):
                    name = named["name"].value
                    assert name not in declarations, name
                    declarations[name] = (function, named)
    return declarations


def _register_periodic_stub(app, task_name):
    """Retain the real declaration/signature, replacing only its execution body."""
    function, named = _periodic_declarations()[task_name]
    # This guard catches a future publisher override that would require parity
    # handling; worker retry/ack settings do not affect producer messages.
    assert not _PUBLICATION_ATTRIBUTES.intersection(named), task_name
    declaration = deepcopy(function)
    declaration.decorator_list = []
    declaration.returns = None
    for argument in (
        *declaration.args.posonlyargs,
        *declaration.args.args,
        *declaration.args.kwonlyargs,
        *([declaration.args.vararg] if declaration.args.vararg else []),
        *([declaration.args.kwarg] if declaration.args.kwarg else []),
    ):
        argument.annotation = None
    declaration.body = ast.parse("raise AssertionError('worker body must not execute')").body
    namespace = {"__name__": task_name.rsplit(".", 1)[0]}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[declaration], type_ignores=[])), "<periodic-stub>", "exec"), namespace)
    options = {key: ast.literal_eval(named[key]) for key in ("bind", "ignore_result") if key in named}
    if "ignore_result" in options:
        assert options["ignore_result"] is True
    app.tasks.pop(task_name, None)
    return app.task(name=task_name, shared=False, lazy=False, **options)(namespace[function.name])


def _publication(app, entry_name, definition, monkeypatch, *, registered):
    if registered:
        _register_periodic_stub(app, definition["task"])
    else:
        # The full suite may already have loaded shared tasks in its parent.
        # The fresh subprocess test separately proves the production registry.
        app.tasks.pop(definition["task"], None)
        assert definition["task"] not in app.tasks
    monkeypatch.setattr(app, "now", lambda: _NOW)
    on_task_call = Mock()
    monkeypatch.setattr(app.backend, "on_task_call", on_task_call)
    options = {
        **definition.get("options", {}),
        "task_id": "periodic-parity-id",
        "reply_to": "periodic-parity-reply",
    }
    entry = ScheduleEntry(
        name=entry_name,
        task=definition["task"],
        schedule=definition["schedule"],
        args=definition.get("args", ()),
        kwargs=definition.get("kwargs", {}),
        options=options,
        app=app,
    )
    scheduler = bounded_beat.BoundedCanaryScheduler(app=app, lazy=True)
    # Exercise the real scheduler dispatch branch with no persistent schedule
    # writes or canary network inspection. Publication itself uses Kombu memory.
    monkeypatch.setattr(bounded_beat, "canary_queue_has_capacity", lambda *_args: True)
    monkeypatch.setattr(scheduler, "should_sync", lambda: False)
    with app.connection_for_write() as connection, connection.channel() as channel:
        result = scheduler.apply_async(entry, producer=Producer(channel), advance=False)
        queue = app.amqp.router.route(options, definition["task"])["queue"].name
        wire = channel._get(queue)
        wire["properties"].pop("delivery_tag")
    return wire, result.ignored, on_task_call.call_count


@pytest.fixture
def publication_apps():
    registered = Celery("beat-registered-parity", set_as_current=False)
    fallback = BeatCelery("beat-fallback-parity", set_as_current=False)
    for app in (registered, fallback):
        app.conf.update({
            **deepcopy(dict(beat_app.conf)),
            "broker_url": "memory://",
            "result_backend": "cache+memory://",
        })
    try:
        yield registered, fallback
    finally:
        registered.close()
        fallback.close()


@pytest.mark.parametrize("entry_name", tuple(worker_app.conf.beat_schedule))
def test_every_schedule_entry_has_registered_message_parity(entry_name, publication_apps, monkeypatch):
    registered, fallback = publication_apps
    definition = worker_app.conf.beat_schedule[entry_name]
    old = _publication(registered, entry_name, definition, monkeypatch, registered=True)
    new = _publication(fallback, entry_name, definition, monkeypatch, registered=False)
    assert old == new
    assert old[1:] == (True, 0)


@pytest.mark.parametrize("ignore_result", [True, False])
@pytest.mark.parametrize("delay", [{"countdown": 20}, {"eta": datetime(2026, 10, 9, 12, 1, tzinfo=timezone.utc)}])
def test_entry_options_preserve_producer_metadata_and_result_override(ignore_result, delay, publication_apps, monkeypatch):
    registered, fallback = publication_apps
    definition = {
        **worker_app.conf.beat_schedule["dispatch-due-feeds"],
        "options": {
            "ignore_result": ignore_result,
            "headers": {"diagnostic-contract": "periodic-parity"},
            "priority": 4,
            "serializer": "json",
            "delivery_mode": 1,
            "compression": "gzip",
            **delay,
            "expires": 90,
            "time_limit": 60,
            "soft_time_limit": 50,
            "shadow": "periodic-parity-shadow",
            "argsrepr": "periodic-args",
            "kwargsrepr": "periodic-kwargs",
            "link": {"task": "periodic-callback", "args": (), "kwargs": {}},
            "link_error": {"task": "periodic-errback", "args": (), "kwargs": {}},
        },
    }
    old = _publication(registered, "options-parity", definition, monkeypatch, registered=True)
    new = _publication(fallback, "options-parity", definition, monkeypatch, registered=False)
    assert old == new
    assert old[1:] == (ignore_result, int(not ignore_result))


@pytest.mark.parametrize("queue", [QUEUE_AI, QUEUE_AI_REPORTS, QUEUE_AI_REPORTS_EDITORIAL])
def test_optional_ai_canaries_preserve_queue_arguments_and_expiry(queue, publication_apps, monkeypatch):
    registered, fallback = publication_apps
    template = next(entry for entry in worker_app.conf.beat_schedule.values() if entry["task"] == bounded_beat.CANARY_TASK)
    definition = {
        **template,
        "args": (queue,),
        "options": {**template["options"], "queue": queue},
    }
    old = _publication(registered, "ai-canary-parity", definition, monkeypatch, registered=True)
    new = _publication(fallback, "ai-canary-parity", definition, monkeypatch, registered=False)
    assert old == new
    assert old[0]["properties"]["delivery_info"]["routing_key"] == queue
    assert old[0]["headers"]["expires"] is not None


def test_scheduler_configuration_is_an_independent_copy_of_worker_configuration(tmp_path):
    # Celery lazily normalizes Queue declarations in place when an app's router
    # is used. Compare initial declarations in a fresh interpreter so earlier
    # tests cannot change the observed stage of either app's configuration.
    script = """
from app.tasks.beat_app import beat_app
from app.tasks.celery_app import celery_app as worker_app

old = dict(worker_app.conf)
new = dict(beat_app.conf)
assert len(old.pop('include')) == 15
assert not old.pop('imports')
assert not new.pop('include')
assert not new.pop('imports')
assert old == new
for key in ('beat_schedule', 'task_routes', 'task_queues', 'broker_transport_options', 'result_backend_transport_options'):
    assert worker_app.conf[key] is not beat_app.conf[key]

# Exercise the exact one-sided mutation seen in the full suite, then compare
# effective routing after both apps have initialized their routing tables.
beat_app.amqp.queues
assert worker_app.conf.task_queues != beat_app.conf.task_queues
worker_app.amqp.queues
assert worker_app.conf.task_queues == beat_app.conf.task_queues
for definition in worker_app.conf.beat_schedule.values():
    options = definition.get('options', {})
    old_route = worker_app.amqp.router.route(dict(options), definition['task'])
    new_route = beat_app.amqp.router.route(dict(options), definition['task'])
    assert old_route == new_route
assert worker_app.finalized is False
print('independent_config_and_effective_routes=passed')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=_isolated_environment(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "independent_config_and_effective_routes=passed"


def test_unregistered_sends_follow_configured_result_default_and_honor_override(publication_apps, monkeypatch):
    _registered, fallback = publication_apps
    fallback.conf.task_ignore_result = False
    definition = worker_app.conf.beat_schedule["dispatch-due-feeds"]
    assert _publication(fallback, "configured-result-default", definition, monkeypatch, registered=False)[1:] == (False, 1)
    definition = {**definition, "options": {"ignore_result": True}}
    assert _publication(fallback, "configured-result-override", definition, monkeypatch, registered=False)[1:] == (True, 0)


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_watchdog_selected_app_initializes_without_worker_implementation_imports(tmp_path, ai_enabled):
    script = """
import sys
from celery.app.utils import find_app
from celery.apps.beat import Beat
from celery.loaders.base import BaseLoader
from app.tasks.beat_watchdog import BEAT_COMMAND_PREFIX
from app.tasks.celery_app import celery_app as worker_app

implementation_modules = tuple(worker_app.conf.include)
assert len(implementation_modules) == 15
assert not any(name in sys.modules for name in implementation_modules)
original_import = BaseLoader.import_task_module

def refuse_worker_implementation(self, module):
    if module in implementation_modules:
        raise AssertionError('Beat attempted worker implementation import: ' + module)
    return original_import(self, module)

BaseLoader.import_task_module = refuse_worker_implementation
selected_app = find_app(BEAT_COMMAND_PREFIX[2])
Beat(app=selected_app).init_loader()
assert selected_app is not worker_app
assert not any(name in sys.modules for name in implementation_modules)
assert tuple(worker_app.conf.include) == implementation_modules
assert worker_app.finalized is False
assert not any(entry['task'] in selected_app.tasks for entry in selected_app.conf.beat_schedule.values())
assert len(selected_app.conf.beat_schedule) == (34 if sys.argv[1] == 'true' else 31)
print('selected_beat_loader_without_worker_imports=passed')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(ai_enabled).lower()],
        cwd=tmp_path,
        env=_isolated_environment(ai_enabled=ai_enabled),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "selected_beat_loader_without_worker_imports=passed"


def test_selected_scheduler_retains_bounded_admission_and_heartbeat(publication_apps, monkeypatch, tmp_path):
    _registered, fallback = publication_apps
    fallback.conf.beat_schedule = {
        "canary": {
            "task": bounded_beat.CANARY_TASK,
            "schedule": 30,
            "args": ("processing",),
            "options": {"queue": "processing", "expires": 60},
        }
    }
    settings = get_settings()
    assert settings.beat_watchdog_startup_grace_seconds == 240
    command = beat_watchdog.build_beat_command(settings)
    scheduler_name = next(argument.split("=", 1)[1] for argument in command if argument.startswith("--scheduler="))
    client = Mock()
    monkeypatch.setattr(beat_scheduler, "redis_client_from_url", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(bounded_beat, "canary_queue_has_capacity", lambda *_args: False)
    scheduler = symbol_by_name(scheduler_name)(app=fallback, schedule_filename=str(tmp_path / "schedule"))
    producer = Mock()
    try:
        assert scheduler.apply_async(scheduler.schedule["canary"], producer=producer) is None
        assert scheduler.schedule["canary"].total_run_count == 1
        producer.publish.assert_not_called()
        monkeypatch.setattr(bounded_beat.BoundedCanaryScheduler, "tick", lambda *_args, **_kwargs: 7.5)
        assert scheduler.tick() == 7.5
        client.set.assert_called_once()
    finally:
        scheduler.close()

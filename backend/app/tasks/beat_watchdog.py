import argparse
import logging
import signal
import subprocess
import time
from collections.abc import Callable, Sequence

from app.core.config import Settings, get_settings
from app.core.logging_config import configure_logging, log_configuration_summary
from app.services.beat_heartbeat import BeatHeartbeatSnapshot, read_beat_heartbeat

logger = logging.getLogger(__name__)

BEAT_COMMAND_PREFIX = (
    "celery",
    "-A",
    "app.tasks.celery_app.celery_app",
    "beat",
)


def build_beat_command(settings: Settings) -> tuple[str, ...]:
    return (
        *BEAT_COMMAND_PREFIX,
        f"--loglevel={settings.log_level}",
        "--scheduler=app.tasks.beat_scheduler:WatchdogPersistentScheduler",
    )


def load_scheduler_heartbeat(settings: Settings) -> BeatHeartbeatSnapshot:
    return read_beat_heartbeat(
        redis_url=settings.redis_url,
        heartbeat_key=settings.beat_scheduler_heartbeat_key,
        stale_after_seconds=settings.beat_heartbeat_stale_after_seconds,
    )


def stop_process(process, *, timeout_seconds: float, signal_number: int = signal.SIGTERM) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal_number)
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        logger.error("beat_watchdog_force_kill timeout_seconds=%s", timeout_seconds)
        process.kill()
        process.wait(timeout=timeout_seconds)


def monitor_beat_process(
    process,
    *,
    heartbeat_loader: Callable[[], BeatHeartbeatSnapshot],
    startup_grace_seconds: float,
    check_interval_seconds: float,
    terminate_timeout_seconds: float,
    shutdown_requested: Callable[[], bool] = lambda: False,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    started_at = monotonic()
    while True:
        return_code = process.poll()
        if return_code is not None:
            return 0 if shutdown_requested() else return_code

        if shutdown_requested():
            stop_process(process, timeout_seconds=terminate_timeout_seconds)
            return 0

        if monotonic() - started_at >= startup_grace_seconds:
            snapshot = heartbeat_loader()
            if not snapshot.ok:
                logger.error(
                    "beat_watchdog_unhealthy reason=%s heartbeat_at=%s age_seconds=%s",
                    snapshot.reason,
                    snapshot.heartbeat_at,
                    snapshot.age_seconds,
                )
                stop_process(process, timeout_seconds=terminate_timeout_seconds)
                return 1

        sleep(check_interval_seconds)


def run_beat(settings: Settings, command: Sequence[str] | None = None) -> int:
    shutdown_signal: int | None = None
    process = None

    def request_shutdown(signal_number, _frame) -> None:
        nonlocal shutdown_signal
        shutdown_signal = signal_number

    previous_handlers = {
        signal_number: signal.signal(signal_number, request_shutdown)
        for signal_number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        process = subprocess.Popen(list(command or build_beat_command(settings)))
        return monitor_beat_process(
            process,
            heartbeat_loader=lambda: load_scheduler_heartbeat(settings),
            startup_grace_seconds=settings.beat_watchdog_startup_grace_seconds,
            check_interval_seconds=settings.beat_watchdog_check_interval_seconds,
            terminate_timeout_seconds=settings.beat_watchdog_terminate_timeout_seconds,
            shutdown_requested=lambda: shutdown_signal is not None,
        )
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)
        if process is not None and process.poll() is None:
            stop_process(process, timeout_seconds=settings.beat_watchdog_terminate_timeout_seconds)


def check_beat(settings: Settings) -> int:
    snapshot = load_scheduler_heartbeat(settings)
    if snapshot.ok:
        return 0
    logger.error(
        "beat_healthcheck_failed reason=%s heartbeat_at=%s age_seconds=%s",
        snapshot.reason,
        snapshot.heartbeat_at,
        snapshot.age_seconds,
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or health-check the Celery Beat watchdog.")
    parser.add_argument("--check", action="store_true", help="Exit based on the current Beat heartbeat.")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    log_configuration_summary(settings, logger=logger)
    return check_beat(settings) if args.check else run_beat(settings)


if __name__ == "__main__":
    raise SystemExit(main())

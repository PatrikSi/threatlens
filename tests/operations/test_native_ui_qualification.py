"""Pure evidence/host guards; synthetic results never qualify real browsers."""
from __future__ import annotations

import copy
from email.headerregistry import Address
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib


OPERATIONS = Path(__file__).resolve().parents[2] / "scripts/operations"
sys.path.insert(0, str(OPERATIONS))
try:
    SPEC = importlib.util.spec_from_file_location(
        "native_ui_qualification_controls", OPERATIONS / "run_native_ui_qualification.py",
    )
    assert SPEC is not None and SPEC.loader is not None
    QUALIFY = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(QUALIFY)
finally:
    sys.path.remove(str(OPERATIONS))


def retained_steps():
    names = [
        ("light", "desktop", "real local cookie login"),
        ("light", "desktop", "route /settings/access"),
        ("light", "desktop", "article preview and team assessment navigation"),
        ("light", "desktop", "reviewed publication preview and consumer contrast"),
        ("light", "mobile", "mobile route /settings/access"),
        ("dark", "desktop", "real local cookie login"),
        ("dark", "desktop", "route /settings/access"),
        ("dark", "desktop", "article preview and team assessment navigation"),
        ("dark", "desktop", "reviewed publication preview and consumer contrast"),
        ("dark", "desktop", "provider draft tab retention and discard navigation"),
        ("dark", "mobile", "mobile route /settings/access"),
    ]
    return [(engine, *row) for engine in ["chromium", "firefox", "webkit"] for row in names]


def host_sample():
    return {
        "available_memory_bytes": 3 * 1024**3,
        "cpu": {"some": 25.0},
        "memory": {"some": 1.0, "full": 0.5},
        "io": {"some": 5.0, "full": 1.0},
        "docker_seconds": 5.0,
    }


def result_steps():
    return [
        dict(zip(["engine", "theme", "layout", "name"], fields),
             status="passed", elapsedSeconds=0.125, details={})
        for fields in retained_steps()
    ]


def result():
    return {
        "sourceSha": QUALIFY.SOURCE, "version": "2.1.0", "status": "passed",
        "runtime": {
            "playwright": "1.63.0",
            "engineVersions": {engine: "synthetic-control" for engine in ["chromium", "firefox", "webkit"]},
        },
        "steps": result_steps(),
        "pageErrors": [], "consoleErrors": [], "apiServerErrors": [],
        "blockedExternalRequests": [], "createdSyntheticResources": [], "cleanup": [],
        "summary": {
            "executedChecks": 33, "expectedCheckCount": 33, "expectedCountMatched": True,
            "passed": 33, "failed": 0, "pageErrors": 0, "consoleErrors": 0,
            "apiServerErrors": 0, "blockedExternalRequests": 0,
            "createdSyntheticResources": 0, "cleanupFailures": 0,
        },
    }


def png(width):
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0" + b"\0" * (width * 4)))
        + chunk(b"IEND", b"")
    )


def complete_surfaces(value, directory):
    number = 0
    for step in value["steps"]:
        count = 0 if step["name"] == "real local cookie login" else (
            2 if step["name"] == "article preview and team assessment navigation" else 1
        )
        surfaces = []
        for _ in range(count):
            number += 1
            name = f"synthetic-control-{number}.png"
            (directory / name).write_bytes(png(390 if step["layout"] == "mobile" else 1440))
            surfaces.append({"axeViolations": [], "axeScope": "Synthetic control", "screenshot": name})
        step["details"] = {"surfaces": surfaces}
    return number


class NativeFixtureLoginInput(unittest.TestCase):
    def test_generated_identity_uses_a_reserved_example_domain_usable_for_login(self):
        # .test/.invalid are rejected by the real LoginRequest EmailStr. The
        # disposable account uses an IANA example domain without mail delivery.
        value = QUALIFY.FIXTURE_EMAIL
        address = Address(addr_spec=value)
        self.assertEqual(address.addr_spec, value)
        self.assertIn(address.domain, {"example.com", "example.net", "example.org"})
        self.assertRegex(address.username, r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")


class NativeReadinessAdmission(unittest.TestCase):
    def test_ready_and_only_explicit_startup_unavailable_are_distinguished(self):
        self.assertIs(QUALIFY.readiness_admission(200, "application/json", b'{"ok":true}'), True)
        self.assertIs(QUALIFY.readiness_admission(503, "application/json", b'{"ok":false}'), False)

    def test_legitimate_ready_health_fields_remain_accepted(self):
        body = b'{"ok":true,"checks":{"database":true,"redis":true},"version":"2.1.0"}'
        self.assertIs(QUALIFY.readiness_admission(200, "application/json", body), True)

    def test_other_statuses_and_noninteger_status_are_rejected(self):
        for status in [199, 201, 204, 301, 400, 401, 403, 404, 429, 500, 502, 504, True, "200", 200.0]:
            with self.subTest(status=status), self.assertRaises(ValueError):
                QUALIFY.readiness_admission(status, "application/json", b'{"ok":true}')

    def test_misleading_status_body_pairs_are_rejected(self):
        for status, body in [
            (200, b'{"ok":false}'), (503, b'{"ok":true}'),
            (200, b'{"ok":1}'), (503, b'{"ok":0}'),
            (200, b'{"ok":"true"}'), (503, b'{"ok":"false"}'),
        ]:
            with self.subTest(status=status, body=body), self.assertRaises(ValueError):
                QUALIFY.readiness_admission(status, "application/json", body)

    def test_malformed_wrong_content_type_and_oversized_body_are_rejected(self):
        for content_type, body in [
            ("text/html", b'{"ok":true}'), ("text/plain", b'{"ok":true}'),
            ("", b'{"ok":true}'), (None, b'{"ok":true}'),
            ("application/json", b''), ("application/json", b'not-json'),
            ("application/json", b'null'), ("application/json", b'[]'),
            ("application/json", b'true'), ("application/json", b'123'),
            ("application/json", b'{}'), ("application/json", b'{"ok":null}'),
            ("application/json", b'{"ok":true}\xff'),
            ("application/json", b' ' * 2_000_001),
        ]:
            with self.subTest(content_type=content_type, body_length=len(body)), self.assertRaises(ValueError):
                QUALIFY.readiness_admission(200, content_type, body)

    def test_transient_unavailable_uses_only_remaining_original_window(self):
        now, reads, sleeps, attempts = [60.0], [], [], []
        responses = [(503, b'{"ok":false}'), (503, b'{"ok":false}'), (200, b'{"ok":true}')]

        def read(timeout):
            reads.append(timeout)
            now[0] += 2
            status, body = responses.pop(0)
            return status, "application/json", body

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        QUALIFY.wait_readiness(read, 240, attempts, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(reads, [15, 15, 15])
        self.assertEqual(sleeps, [5, 5])
        self.assertEqual([attempt["http_status"] for attempt in attempts], [503, 503, 200])
        self.assertEqual([attempt["ok"] for attempt in attempts], [False, False, True])
        self.assertEqual(now[0], 76)

    def test_expired_original_window_performs_no_request(self):
        calls, attempts = [], []
        with self.assertRaises(TimeoutError):
            QUALIFY.wait_readiness(lambda timeout: calls.append(timeout), 240, attempts,
                                   clock=lambda: 240, sleep=lambda seconds: calls.append(seconds))
        self.assertEqual((calls, attempts), ([], []))

    def test_last_read_and_poll_cannot_reset_startup_budget(self):
        now, reads, sleeps, attempts = [239.0], [], [], []

        def read(timeout):
            reads.append(timeout)
            now[0] += 0.25
            return 503, "application/json", b'{"ok":false}'

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        with self.assertRaises(TimeoutError):
            QUALIFY.wait_readiness(read, 240, attempts, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(reads, [1.0])
        self.assertEqual(sleeps, [0.75])
        self.assertEqual(now[0], 240)
        self.assertEqual([attempt["http_status"] for attempt in attempts], [503])

    def test_ready_response_after_original_deadline_is_not_accepted(self):
        now, attempts = [239.0], []

        def read(timeout):
            self.assertEqual(timeout, 1)
            now[0] += 2
            return 200, "application/json", b'{"ok":true}'

        with self.assertRaises(TimeoutError):
            QUALIFY.wait_readiness(read, 240, attempts, clock=lambda: now[0],
                                   sleep=lambda _seconds: self.fail("Ready response must not poll"))
        self.assertEqual([attempt["http_status"] for attempt in attempts], [200])

    def test_unsupported_response_retains_status_and_never_polls(self):
        attempts = []
        with self.assertRaises(ValueError):
            QUALIFY.wait_readiness(lambda _timeout: (500, "application/json", b'{"ok":false}'),
                                   240, attempts, clock=lambda: 10,
                                   sleep=lambda _seconds: self.fail("Only explicit startup 503 may poll"))
        self.assertEqual([attempt["http_status"] for attempt in attempts], [500])


class NativeHostGuards(unittest.TestCase):
    def test_exact_declared_limits_are_eligible(self):
        self.assertTrue(QUALIFY.eligible_host(host_sample()))

    def test_each_exceeded_boundary_is_ineligible(self):
        for path, value in [
            (("available_memory_bytes",), 3 * 1024**3 - 1),
            (("cpu", "some"), 25.0001), (("memory", "some"), 1.0001),
            (("memory", "full"), 0.5001), (("io", "some"), 5.0001),
            (("io", "full"), 1.0001), (("docker_seconds",), 5.0001),
        ]:
            with self.subTest(path=path):
                sample = host_sample()
                target = sample if len(path) == 1 else sample[path[0]]
                target[path[-1]] = value
                self.assertFalse(QUALIFY.eligible_host(sample))

    def test_invalid_host_samples_fail_closed(self):
        for value in [None, {}, True, [], {"available_memory_bytes": 3 * 1024**3}]:
            with self.subTest(value=value):
                self.assertIs(QUALIFY.eligible_host(value), False)
        for path in [
            ("available_memory_bytes",), ("cpu", "some"), ("memory", "some"),
            ("memory", "full"), ("io", "some"), ("io", "full"), ("docker_seconds",),
        ]:
            for value in [True, "0", float("nan"), float("inf"), -1.0]:
                with self.subTest(path=path, value=value):
                    sample = host_sample()
                    target = sample if len(path) == 1 else sample[path[0]]
                    target[path[-1]] = value
                    self.assertIs(QUALIFY.eligible_host(sample), False)

    def test_invalid_pressure_sample_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pressure"
            for value in ["nan", "inf", "-0.1", "100.1"]:
                with self.subTest(value=value):
                    path.write_text(f"some avg10={value} avg60=0 avg300=0 total=0\n")
                    with self.assertRaises(ValueError):
                        QUALIFY.pressure(path)
            path.write_text("some avg10=25 avg60=0 avg300=0 total=0\nfull avg10=0.5 avg60=0 avg300=0 total=0\n")
            self.assertEqual(QUALIFY.pressure(path), {"some": 25.0, "full": 0.5})


class NativeBrowserProjection(unittest.TestCase):
    def test_exact_original_33_order_and_scope_are_preserved(self):
        self.assertEqual(QUALIFY.expected_steps(), retained_steps())
        self.assertEqual(len(set(QUALIFY.expected_steps())), 33)

    def test_failed_login_omissions_are_retained(self):
        value = result()
        value.pop("summary")
        value["steps"][0]["status"] = "failed"
        value["steps"][0]["error"] = "page.waitForURL: Timeout 30000ms exceeded"
        value["steps"] = value["steps"][:1] + value["steps"][5:]
        projection = QUALIFY.browser_projection(value)
        self.assertEqual(
            {key: projection[key] for key in ["executed", "passed", "failed", "omitted", "summary_available"]},
            {"executed": 29, "passed": 28, "failed": 1, "omitted": 4, "summary_available": False},
        )
        self.assertEqual(projection["steps"][0]["failure_category"], "login_navigation")

    def test_failed_capture_evidence_retains_only_safe_category(self):
        value = result()
        step = value["steps"][18]
        step.update(status="failed", error=(
            "page.screenshot: Timeout at /home/private/raw with password=fixture-secret "
            "and team=09a1c3d0-cf40-42c3-a205-cda9ebc92227"
        ))
        step["details"] = {"url": "http://private-host/team?secret=fixture-secret"}
        projection = QUALIFY.browser_projection(value)
        self.assertEqual(projection["steps"][18]["failure_category"], "screenshot")
        text = json.dumps(projection)
        for private in ["/home/private", "fixture-secret", "09a1c3d0", "private-host"]:
            self.assertNotIn(private, text)
        self.assertEqual((projection["executed"], projection["failed"], projection["omitted"]), (33, 1, 0))

    def test_duplicate_or_reordered_steps_are_rejected(self):
        for change in ["duplicate", "reordered", "unexpected"]:
            with self.subTest(change=change):
                value = result()
                if change == "duplicate":
                    value["steps"].append(copy.deepcopy(value["steps"][0]))
                elif change == "reordered":
                    value["steps"][0], value["steps"][1] = value["steps"][1], value["steps"][0]
                else:
                    value["steps"][0]["name"] = "unqualified replacement check"
                with self.assertRaises(ValueError):
                    QUALIFY.browser_projection(value)

    def test_provenance_mismatch_is_rejected(self):
        for key, replacement in [("sourceSha", "0" * 40), ("version", "2.0.1")]:
            with self.subTest(key=key):
                value = result()
                value[key] = replacement
                with self.assertRaises(ValueError):
                    QUALIFY.browser_projection(value)

    def test_invalid_partial_elapsed_time_is_rejected(self):
        for elapsed in [True, -1, float("nan"), float("inf"), "1"]:
            with self.subTest(elapsed=elapsed):
                value = result()
                value["steps"][0]["elapsedSeconds"] = elapsed
                with self.assertRaises(ValueError):
                    QUALIFY.browser_projection(value)


class NativeBrowserSuccessGuards(unittest.TestCase):
    def test_complete_synthetic_fixture_passes_guard_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory, value = Path(temporary), result()
            self.assertEqual(complete_surfaces(value, directory), 33)
            verified = QUALIFY.validate_browser(value, directory)
            self.assertEqual((verified["executed"], verified["passed"], verified["axe_surfaces"]), (33, 33, 33))
            self.assertEqual(len(verified["screenshots"]), 33)

    def test_failed_incomplete_unmatched_and_noninteger_counters_rejected(self):
        mutations = [
            lambda value: value["steps"].pop(),
            lambda value: value["steps"][0].update(status="failed"),
            lambda value: value["summary"].update(expectedCountMatched=False),
            lambda value: value["summary"].update(expectedCountMatched=1),
            lambda value: value["summary"].update(passed=32, failed=1),
            lambda value: value["summary"].update(executedChecks=32),
            lambda value: value["summary"].update(expectedCheckCount=32),
            lambda value: value["summary"].update(failed=False),
            lambda value: value["runtime"]["engineVersions"].pop("webkit"),
            lambda value: value.update(status="failed"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for mutation in mutations:
                value = result()
                mutation(value)
                with self.assertRaises(ValueError):
                    QUALIFY.validate_browser(value, Path(temporary))

    def test_listener_errors_and_cleanup_failures_rejected(self):
        for field in ["pageErrors", "consoleErrors", "apiServerErrors", "blockedExternalRequests", "createdSyntheticResources"]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                value = result()
                value[field].append({"synthetic": True})
                with self.assertRaises(ValueError):
                    QUALIFY.validate_browser(value, Path(temporary))
        value = result()
        value["summary"]["cleanupFailures"] = 1
        with self.assertRaises(ValueError):
            QUALIFY.validate_browser(value, Path("unused-control-directory"))

    def test_missing_duplicate_unsafe_symlink_capture_and_axe_failures_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            baseline = result()
            complete_surfaces(baseline, directory)
            for change in ["missing-surface", "duplicate-capture", "unsafe-path", "axe", "symlink"]:
                with self.subTest(change=change):
                    value = copy.deepcopy(baseline)
                    first = value["steps"][1]["details"]["surfaces"][0]
                    second = value["steps"][2]["details"]["surfaces"][0]
                    if change == "missing-surface":
                        value["steps"][1]["details"] = {}
                    elif change == "duplicate-capture":
                        second["screenshot"] = first["screenshot"]
                    elif change == "unsafe-path":
                        first["screenshot"] = "../private.png"
                    elif change == "axe":
                        first["axeViolations"] = [{"id": "synthetic-violation"}]
                    else:
                        link = directory / "synthetic-link.png"
                        link.symlink_to(directory / first["screenshot"])
                        first["screenshot"] = link.name
                    with self.assertRaises(ValueError):
                        QUALIFY.validate_browser(value, directory)


if __name__ == "__main__":
    unittest.main()

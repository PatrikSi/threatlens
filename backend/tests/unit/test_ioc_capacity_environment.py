"""Exercise the actual resource-limited test subprocess boundaries."""

import os
import subprocess

import pytest

from tests.unit import test_ioc_extraction as hash_tests
from tests.unit import test_ioc_network_extraction as network_tests


@pytest.mark.parametrize("capacity_test", [
    network_tests.test_dense_punctuation_and_url_boundaries_have_bounded_cpu,
    network_tests.test_large_defanged_inventory_has_bounded_cpu_and_mapping_memory,
    hash_tests.test_dense_hash_document_completes_with_bounded_cpu,
])
def test_capacity_children_exclude_inherited_coverage_without_mutating_parent(
    monkeypatch, capacity_test,
):
    instrumentation = {
        "COV_CORE_SOURCE": "sentinel-coverage-source",
        "COV_CORE_CONFIG": "sentinel-coverage-config",
        "COV_CORE_DATAFILE": "sentinel-coverage-data",
        "COV_CORE_BRANCH": "enabled",
        "COV_CORE_CONTEXT": "sentinel-context",
        "COVERAGE_PROCESS_START": "sentinel-process-config",
    }
    for key, value in instrumentation.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PYTHONPATH", "sentinel-python-path")
    monkeypatch.setenv("THREATLENS_CAPACITY_SENTINEL", "preserve-me")
    calls = []

    def run_child(arguments, **kwargs):
        environment = kwargs.get("env", os.environ)
        assert not any(key.startswith("COV_CORE_") for key in environment)
        assert "COVERAGE_PROCESS_START" not in environment
        assert environment["PYTHONPATH"] == "sentinel-python-path"
        assert environment["THREATLENS_CAPACITY_SENTINEL"] == "preserve-me"
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run_child)
    capacity_test()

    assert len(calls) == 1
    assert all(os.environ[key] == value for key, value in instrumentation.items())

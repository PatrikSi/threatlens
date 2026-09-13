import os

import pytest

from tests.capacity.docker_services import DockerService


@pytest.fixture(scope="session")
def test_database_url():
    assert os.environ.get("THREATLENS_CAPACITY_RUN_ID"), "use run_capacity_baseline.py"
    with DockerService("postgres") as service:
        yield service.url


@pytest.fixture(scope="session")
def capacity_redis_service():
    assert os.environ.get("THREATLENS_CAPACITY_RUN_ID"), "use run_capacity_baseline.py"
    with DockerService("redis") as service:
        yield service


@pytest.fixture(scope="session")
def test_redis_url(capacity_redis_service):
    yield capacity_redis_service.url

"""Unit tests for DockerController.exhaust_resources/restore_resources
(issue #9's real CPU-exhaustion mechanism).

A fake Docker SDK client is monkeypatched in place of docker.from_env() so
these never touch a real Docker daemon — they verify the CPU-quota math,
the exec_run call shape, graceful no-container/exception handling, and that
an auto-restore timer gets scheduled, without waiting on a real timer.
"""

from __future__ import annotations

import pytest
from docker.errors import DockerException

import backend.simulator.docker_controller as docker_controller_module
from backend.simulator.docker_controller import DockerController


class FakeContainer:
    def __init__(self, status="running", host_config=None):
        self.status = status
        self.attrs = {"HostConfig": host_config or {"CpuQuota": 0, "CpuPeriod": 0}}
        self.update_calls: list[dict] = []
        self.exec_calls: list[dict] = []
        self.update_should_raise = False

    def reload(self):
        pass

    def update(self, **kwargs):
        if self.update_should_raise:
            raise DockerException("update failed")
        self.update_calls.append(kwargs)

    def exec_run(self, cmd, detach=False):
        self.exec_calls.append({"cmd": cmd, "detach": detach})
        return None


class FakeContainersCollection:
    def __init__(self, containers):
        self._containers = containers

    def list(self, filters=None, all=None):
        return self._containers


class FakeDockerClient:
    def __init__(self, containers):
        self.containers = FakeContainersCollection(containers)

    def ping(self):
        pass


class FakeTimer:
    """Records Timer(delay, fn, args=...) construction; .start() is a no-op
    so tests never actually wait on a real timer."""

    instances: list["FakeTimer"] = []

    def __init__(self, delay, fn, args=()):
        self.delay = delay
        self.fn = fn
        self.args = args
        self.started = False
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True


@pytest.fixture(autouse=True)
def clear_fake_timers():
    FakeTimer.instances.clear()
    yield
    FakeTimer.instances.clear()


@pytest.fixture
def controller_and_container(monkeypatch):
    fake_container = FakeContainer()
    fake_client = FakeDockerClient([fake_container])
    monkeypatch.setattr(docker_controller_module.docker, "from_env", lambda: fake_client)
    monkeypatch.setattr(docker_controller_module.threading, "Timer", FakeTimer)
    controller = DockerController()
    return controller, fake_container


class TestExhaustResources:
    @pytest.mark.asyncio
    async def test_caps_cpu_quota_and_spawns_workers(self, controller_and_container):
        controller, container = controller_and_container
        result = await controller.exhaust_resources(
            "payment-service", cpu_quota_pct=25, workers=2, duration_seconds=60
        )

        assert result == {
            "started": True,
            "cpu_quota_pct": 25,
            "workers": 2,
            "duration_seconds": 60,
        }
        assert container.update_calls == [{"cpu_quota": 25000, "cpu_period": 100_000}]
        assert len(container.exec_calls) == 2
        for call in container.exec_calls:
            assert call["detach"] is True
            assert call["cmd"][0] == "python3"
            assert "time.time()+60" in call["cmd"][2]

    @pytest.mark.asyncio
    async def test_schedules_auto_restore_timer(self, controller_and_container):
        controller, container = controller_and_container
        await controller.exhaust_resources("payment-service", duration_seconds=45)

        assert len(FakeTimer.instances) == 1
        timer = FakeTimer.instances[0]
        assert timer.delay == 45
        assert timer.fn == controller._sync_restore_resources
        assert timer.args == ("payment-service", {"cpu_quota": -1, "cpu_period": 100_000})
        assert timer.started is True

    @pytest.mark.asyncio
    async def test_captures_previous_quota_for_restore(self, monkeypatch):
        container = FakeContainer(host_config={"CpuQuota": 50000, "CpuPeriod": 100_000})
        fake_client = FakeDockerClient([container])
        monkeypatch.setattr(docker_controller_module.docker, "from_env", lambda: fake_client)
        monkeypatch.setattr(docker_controller_module.threading, "Timer", FakeTimer)
        controller = DockerController()

        await controller.exhaust_resources("payment-service", cpu_quota_pct=10, duration_seconds=30)

        timer = FakeTimer.instances[0]
        assert timer.args == ("payment-service", {"cpu_quota": 50000, "cpu_period": 100_000})

    @pytest.mark.asyncio
    async def test_container_not_found_returns_started_false(self, monkeypatch):
        fake_client = FakeDockerClient([])
        monkeypatch.setattr(docker_controller_module.docker, "from_env", lambda: fake_client)
        controller = DockerController()

        result = await controller.exhaust_resources("nonexistent-service")
        assert result == {"started": False, "reason": "container not found"}
        assert FakeTimer.instances == []

    @pytest.mark.asyncio
    async def test_docker_exception_during_update_returns_started_false(self, controller_and_container):
        controller, container = controller_and_container
        container.update_should_raise = True

        result = await controller.exhaust_resources("payment-service")
        assert result["started"] is False
        assert "update failed" in result["reason"]
        assert container.exec_calls == []
        assert FakeTimer.instances == []


class TestRestoreResources:
    @pytest.mark.asyncio
    async def test_restores_previous_cpu_quota(self, controller_and_container):
        controller, container = controller_and_container
        ok = await controller.restore_resources(
            "payment-service", {"cpu_quota": -1, "cpu_period": 100_000}
        )
        assert ok is True
        assert container.update_calls == [{"cpu_quota": -1, "cpu_period": 100_000}]

    @pytest.mark.asyncio
    async def test_container_not_found_returns_false(self, monkeypatch):
        fake_client = FakeDockerClient([])
        monkeypatch.setattr(docker_controller_module.docker, "from_env", lambda: fake_client)
        controller = DockerController()

        ok = await controller.restore_resources("nonexistent-service", {"cpu_quota": -1})
        assert ok is False

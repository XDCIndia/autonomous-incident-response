"""Unit tests for RemediationEngine's real scale_up implementation (issue #10).

A minimal fake stands in for DockerController — real CPU-quota-restore
behavior itself is covered by test_docker_controller_exhaust_resources.py.
"""

from __future__ import annotations

import pytest

from backend.contracts import RemediationRequest
from backend.remediation.actions import RemediationEngine


class FakeDockerController:
    def __init__(self, restore_success: bool = True, health_before: str = "unhealthy", health_after: str = "healthy"):
        self.restore_success = restore_success
        self.health_calls: list[str] = []
        self.restore_calls: list[str] = []
        self.wait_calls: list[str] = []
        self._health_sequence = iter([health_before, health_after])
        self._last_health = health_before

    async def check_health(self, service_name):
        self.health_calls.append(service_name)
        try:
            self._last_health = next(self._health_sequence)
        except StopIteration:
            pass
        return {"health": self._last_health}

    async def restore_resources(self, service_name):
        self.restore_calls.append(service_name)
        return self.restore_success

    async def wait_for_health(self, service_name, retries=10, delay=2.0):
        self.wait_calls.append(service_name)
        return True


class TestRealScaleUp:
    @pytest.mark.asyncio
    async def test_uses_real_path_when_docker_controller_set(self):
        docker_ctl = FakeDockerController()
        engine = RemediationEngine(docker_controller=docker_ctl)
        request = RemediationRequest(action="scale_up", target_service="payment-service")

        result = await engine.execute(request)

        assert result.success is True
        assert result.action == "scale_up"
        assert docker_ctl.restore_calls == ["payment-service"]
        assert docker_ctl.wait_calls == ["payment-service"]
        assert "Lifted CPU quota" in result.message

    @pytest.mark.asyncio
    async def test_before_after_state_reflects_health_check(self):
        docker_ctl = FakeDockerController(health_before="unhealthy", health_after="healthy")
        engine = RemediationEngine(docker_controller=docker_ctl)
        request = RemediationRequest(action="scale_up", target_service="payment-service")

        result = await engine.execute(request)

        assert result.before_state["status"] == "unhealthy"
        assert result.after_state["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_restore_failure_is_reported_and_skips_wait(self):
        docker_ctl = FakeDockerController(restore_success=False)
        engine = RemediationEngine(docker_controller=docker_ctl)
        request = RemediationRequest(action="scale_up", target_service="payment-service")

        result = await engine.execute(request)

        assert result.success is False
        assert "Failed to lift" in result.message
        assert docker_ctl.wait_calls == []

    @pytest.mark.asyncio
    async def test_no_docker_controller_falls_back_to_mock(self):
        engine = RemediationEngine()  # no docker_controller — pre-existing mock path
        request = RemediationRequest(action="scale_up", target_service="payment-service")

        result = await engine.execute(request)

        assert result.success is True
        assert engine.get_state()["scale_count"] == 2

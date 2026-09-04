"""Unit tests for inject_resource_exhaustion (issue #9).

A minimal fake stands in for DockerController — real Docker behavior itself
is covered by test_docker_controller_exhaust_resources.py.
"""

from __future__ import annotations

import pytest

from backend.simulator.scenarios import FaultInjectionResult, inject_resource_exhaustion


class FakeDockerController:
    def __init__(self, exhaust_result: dict):
        self._exhaust_result = exhaust_result
        self.exhaust_calls: list[dict] = []

    async def exhaust_resources(self, service_name, *, cpu_quota_pct, workers, duration_seconds):
        self.exhaust_calls.append(
            {
                "service_name": service_name,
                "cpu_quota_pct": cpu_quota_pct,
                "workers": workers,
                "duration_seconds": duration_seconds,
            }
        )
        return self._exhaust_result


class TestMockMode:
    @pytest.mark.asyncio
    async def test_no_controller_defaults_to_payment_service(self):
        result = await inject_resource_exhaustion()
        assert isinstance(result, FaultInjectionResult)
        assert result.service == "payment-service"
        assert result.docker_performed is False
        assert result.metadata == {}

    @pytest.mark.asyncio
    async def test_no_controller_returns_deterministic_signals(self):
        result = await inject_resource_exhaustion(service="payment-service")
        assert len(result.signals) == 3
        types = {s.event_type for s in result.signals}
        assert types == {"cpu_usage", "latency", "log_error"}
        cpu_signal = next(s for s in result.signals if s.event_type == "cpu_usage")
        assert cpu_signal.value == 0.95
        assert cpu_signal.metadata["root_cause_hint"] == "resource_exhaustion"
        assert all(s.source == "payment-service" for s in result.signals)


class TestRealMode:
    @pytest.mark.asyncio
    async def test_successful_injection_marks_docker_performed(self):
        fake = FakeDockerController(
            {"started": True, "cpu_quota_pct": 25, "workers": 2, "duration_seconds": 60}
        )
        result = await inject_resource_exhaustion(service="payment-service", docker_controller=fake)

        assert result.docker_performed is True
        assert result.metadata == {"cpu_quota_pct": 25, "workers": 2, "duration_seconds": 60}
        # Metadata is also folded into the cpu_usage signal for the investigator agents.
        cpu_signal = next(s for s in result.signals if s.event_type == "cpu_usage")
        assert cpu_signal.metadata["cpu_quota_pct"] == 25

    @pytest.mark.asyncio
    async def test_custom_params_passed_through(self):
        fake = FakeDockerController({"started": True})
        await inject_resource_exhaustion(
            service="db-service",
            cpu_quota_pct=10,
            workers=3,
            duration_seconds=30,
            docker_controller=fake,
        )
        assert fake.exhaust_calls == [
            {
                "service_name": "db-service",
                "cpu_quota_pct": 10,
                "workers": 3,
                "duration_seconds": 30,
            }
        ]

    @pytest.mark.asyncio
    async def test_container_not_found_degrades_gracefully(self):
        fake = FakeDockerController({"started": False, "reason": "container not found"})
        result = await inject_resource_exhaustion(service="order-service", docker_controller=fake)

        # Still returns the same signal shape as mock mode — no crash, no
        # silent lie about docker_performed.
        assert result.docker_performed is False
        assert result.metadata == {}
        assert len(result.signals) == 3

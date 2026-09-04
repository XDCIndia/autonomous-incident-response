"""Unit tests for VerificationInterface (issue #6).

No real Docker/network — backend.simulator.health_checker.verify_service_health
is monkeypatched with a fake so these exercise the real-check code path
(URL construction, port mapping, result translation, exception fallback)
without touching Docker or the network.
"""

from __future__ import annotations

import pytest

import backend.simulator.health_checker as health_checker_module
from backend.orchestrator.nodes import VerificationInterface
from backend.simulator.health_checker import VerificationResult as HealthCheckerResult
from backend.contracts import Incident, RemediationRequest, RemediationResult


@pytest.fixture
def incident_with_remediation():
    incident = Incident(service_name="payment-service")
    incident.remediation_request = RemediationRequest(
        action="rollback_deploy",
        target_service="payment-service",
    )
    incident.remediation_result = RemediationResult(action="rollback_deploy", success=True)
    return incident


class FakeDockerController:
    """Never actually touched — verify_service_health is monkeypatched, so
    this only needs to exist as a non-None sentinel."""


class TestStubFallback:
    @pytest.mark.asyncio
    async def test_no_docker_ctl_success_matches_original_stub(self, incident_with_remediation):
        verification = VerificationInterface()
        result = await verification.verify(incident_with_remediation)
        assert result.verified is True
        assert result.checks_passed == 3
        assert result.checks_total == 3
        assert result.recovered_metrics["error_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_no_docker_ctl_failure_matches_original_stub(self, incident_with_remediation):
        incident_with_remediation.remediation_result.success = False
        verification = VerificationInterface()
        result = await verification.verify(incident_with_remediation)
        assert result.verified is False
        assert result.checks_passed == 1
        assert result.recovered_metrics["latency_ms"] == 2400

    @pytest.mark.asyncio
    async def test_no_docker_ctl_no_remediation_result_is_unverified(self):
        incident = Incident(service_name="payment-service")
        verification = VerificationInterface()
        result = await verification.verify(incident)
        assert result.verified is False


class TestRealVerification:
    @pytest.mark.asyncio
    async def test_calls_verify_service_health_with_correct_url(
        self, monkeypatch, incident_with_remediation
    ):
        calls = []

        async def fake_verify_service_health(docker_ctl, service_name, health_url="", verify_urls=None):
            calls.append({"docker_ctl": docker_ctl, "service_name": service_name, "health_url": health_url, "verify_urls": verify_urls})
            return HealthCheckerResult(verified=True, checks_passed=1, checks_total=1, message="ok")

        monkeypatch.setattr(health_checker_module, "verify_service_health", fake_verify_service_health)

        docker_ctl = FakeDockerController()
        verification = VerificationInterface(docker_ctl=docker_ctl)
        result = await verification.verify(incident_with_remediation)

        assert len(calls) == 1
        assert calls[0]["docker_ctl"] is docker_ctl
        assert calls[0]["service_name"] == "payment-service"
        assert calls[0]["health_url"] == "http://localhost:5001/health"
        assert result.verified is True

    @pytest.mark.asyncio
    async def test_adds_pay_probe_for_payment_service_recovery_actions(
        self, monkeypatch, incident_with_remediation
    ):
        calls = []

        async def fake_verify_service_health(docker_ctl, service_name, health_url="", verify_urls=None):
            calls.append(verify_urls)
            return HealthCheckerResult(verified=True, checks_passed=1, checks_total=1)

        monkeypatch.setattr(health_checker_module, "verify_service_health", fake_verify_service_health)

        incident_with_remediation.remediation_request.action = "circuit_break"
        verification = VerificationInterface(docker_ctl=FakeDockerController())
        await verification.verify(incident_with_remediation)

        assert calls[0] == ["http://localhost:5001/pay"]

    @pytest.mark.asyncio
    async def test_no_pay_probe_for_non_payment_service(self, monkeypatch, incident_with_remediation):
        calls = []

        async def fake_verify_service_health(docker_ctl, service_name, health_url="", verify_urls=None):
            calls.append(verify_urls)
            return HealthCheckerResult(verified=True, checks_passed=1, checks_total=1)

        monkeypatch.setattr(health_checker_module, "verify_service_health", fake_verify_service_health)

        incident_with_remediation.remediation_request.target_service = "db-service"
        incident_with_remediation.remediation_request.action = "circuit_break"
        verification = VerificationInterface(docker_ctl=FakeDockerController())
        await verification.verify(incident_with_remediation)

        assert calls[0] == []

    @pytest.mark.asyncio
    async def test_unknown_service_falls_back_to_port_5000(self, monkeypatch, incident_with_remediation):
        calls = []

        async def fake_verify_service_health(docker_ctl, service_name, health_url="", verify_urls=None):
            calls.append(health_url)
            return HealthCheckerResult(verified=True, checks_passed=1, checks_total=1)

        monkeypatch.setattr(health_checker_module, "verify_service_health", fake_verify_service_health)

        incident_with_remediation.remediation_request.target_service = "some-new-service"
        verification = VerificationInterface(docker_ctl=FakeDockerController())
        await verification.verify(incident_with_remediation)

        assert calls[0] == "http://localhost:5000/health"

    @pytest.mark.asyncio
    async def test_real_result_translated_into_contracts_verification_result(
        self, monkeypatch, incident_with_remediation
    ):
        async def fake_verify_service_health(docker_ctl, service_name, health_url="", verify_urls=None):
            return HealthCheckerResult(
                verified=False,
                checks_passed=1,
                checks_total=2,
                message="http://localhost:5001/health returned 503",
                recovered_metrics={"docker_health": "unhealthy"},
                metadata={"note": "still recovering"},
            )

        monkeypatch.setattr(health_checker_module, "verify_service_health", fake_verify_service_health)

        verification = VerificationInterface(docker_ctl=FakeDockerController())
        result = await verification.verify(incident_with_remediation)

        assert result.verified is False
        assert result.checks_passed == 1
        assert result.checks_total == 2
        assert result.message == "http://localhost:5001/health returned 503"
        assert result.recovered_metrics == {"docker_health": "unhealthy"}
        assert result.metadata == {"note": "still recovering"}

    @pytest.mark.asyncio
    async def test_exception_during_real_check_falls_back_to_stub(
        self, monkeypatch, incident_with_remediation
    ):
        async def fake_verify_service_health(*args, **kwargs):
            raise ConnectionError("docker daemon unreachable")

        monkeypatch.setattr(health_checker_module, "verify_service_health", fake_verify_service_health)

        verification = VerificationInterface(docker_ctl=FakeDockerController())
        result = await verification.verify(incident_with_remediation)

        # Falls back to the same stub verdict as TestStubFallback — success=True
        # on the fixture's remediation_result.
        assert result.verified is True
        assert result.checks_passed == 3

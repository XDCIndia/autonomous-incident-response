"""Unit tests for VerificationInterface (issue #6).

No real Docker/network — backend.simulator.health_checker.verify_service_health
is monkeypatched with a fake so these exercise the real-check code path
(URL construction, port mapping, result translation, exception fallback)
without touching Docker or the network.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

import httpx

import backend.simulator.health_checker as health_checker_module
from backend.orchestrator.nodes import OrchestratorNodes, VerificationInterface
from backend.simulator.health_checker import VerificationResult as HealthCheckerResult
from backend.contracts import Incident, RemediationRequest, RemediationResult, VerificationResult


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


class _FakeUrlResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeUrlAsyncClient:
    def __init__(self, response: _FakeUrlResponse | None = None, raises: Exception | None = None):
        self._response = response
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *args, **kwargs):
        if self._raises:
            raise self._raises
        return self._response


class TestUrlMonitorVerification:
    """url_monitor-sourced incidents verify against the real target URL
    directly, not the docker/host-port logic — see issue #36."""

    @pytest.fixture
    def url_monitor_incident(self):
        incident = Incident(
            service_name="My App",
            target_url="http://example.com",
            source="url_monitor",
        )
        incident.remediation_request = RemediationRequest(
            action="restart_service", target_service="My App"
        )
        incident.remediation_result = RemediationResult(action="restart_service", success=False)
        return incident

    @pytest.mark.asyncio
    async def test_healthy_url_is_verified(self, monkeypatch, url_monitor_incident):
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeUrlAsyncClient(_FakeUrlResponse(200)))

        # No docker_ctl at all — url_monitor verification never needs one.
        verification = VerificationInterface(docker_ctl=None)
        result = await verification.verify(url_monitor_incident)

        assert result.verified is True
        assert result.recovered_metrics["status_code"] == 200

    @pytest.mark.asyncio
    async def test_5xx_url_is_not_verified(self, monkeypatch, url_monitor_incident):
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeUrlAsyncClient(_FakeUrlResponse(503)))

        verification = VerificationInterface(docker_ctl=None)
        result = await verification.verify(url_monitor_incident)

        assert result.verified is False
        assert result.recovered_metrics["status_code"] == 503

    @pytest.mark.asyncio
    async def test_connection_error_is_not_verified_and_does_not_crash(self, monkeypatch, url_monitor_incident):
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, **k: _FakeUrlAsyncClient(raises=ConnectionError("refused"))
        )

        verification = VerificationInterface(docker_ctl=None)
        result = await verification.verify(url_monitor_incident)

        assert result.verified is False
        assert "refused" in result.message

    @pytest.mark.asyncio
    async def test_simulator_incident_with_target_url_unset_uses_docker_path(self, monkeypatch):
        # source defaults to "simulator" — even if target_url happened to be
        # set, only source == "url_monitor" takes the URL-verification path.
        incident = Incident(service_name="payment-service", target_url="http://example.com")
        incident.remediation_result = RemediationResult(action="restart_service", success=True)

        verification = VerificationInterface(docker_ctl=None)
        result = await verification.verify(incident)

        # Falls to the no-docker stub, not the URL check — proven by the
        # stub's distinctive fixed metrics shape.
        assert result.recovered_metrics.get("error_rate") is not None


class _PoisonVerifier:
    """Stands in for ServiceHealthVerifier (or any future verifier) — its
    verify() must NEVER be called for a url_monitor incident, regardless of
    what self.verification is configured to. Calling it fails the test
    immediately rather than silently producing a wrong-but-plausible result,
    which is exactly how the original bug slipped past every other test:
    VerificationInterface's own branch is correct in isolation, but nothing
    enforced that OrchestratorNodes.verify() would actually reach it instead
    of whatever real_env=auto had swapped in."""

    async def verify(self, incident: Incident) -> HealthCheckerResult:
        raise AssertionError(
            "ServiceHealthVerifier-equivalent.verify() was called for a "
            "url_monitor incident — the safety boundary was bypassed"
        )


def _make_orchestrator_nodes(verification) -> OrchestratorNodes:
    """Minimal OrchestratorNodes for testing the verify() node in isolation
    — only storage/event_bus (used by _emit) and verification matter here."""
    return OrchestratorNodes(
        log_investigator=None,
        metric_investigator=None,
        arbiter=None,
        severity_agent=None,
        reporter=None,
        remediation_engine=None,
        verification=verification,
        storage=AsyncMock(),
        event_bus=AsyncMock(),
        approval_events={},
        approval_decisions={},
    )


class TestUrlMonitorVerificationBypassesConfiguredVerifier:
    """Reproduces the exact production scenario from the validation report:
    real_env=auto detects Docker, so the orchestrator's self.verification is
    a real (here: poisoned) ServiceHealthVerifier — a url_monitor incident
    must still verify target_url directly via the node-level branch in
    OrchestratorNodes.verify(), never reaching that configured verifier."""

    @pytest.fixture
    def url_monitor_state(self):
        incident = Incident(
            service_name="My App",
            target_url="http://example.com",
            source="url_monitor",
        )
        incident.remediation_request = RemediationRequest(action="restart_service", target_service="My App")
        incident.remediation_result = RemediationResult(action="restart_service", success=False)
        return {"incident": incident}

    @pytest.mark.asyncio
    async def test_healthy_target_url_verifies_true_without_touching_configured_verifier(
        self, monkeypatch, url_monitor_state
    ):
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeUrlAsyncClient(_FakeUrlResponse(200)))

        nodes = _make_orchestrator_nodes(_PoisonVerifier())
        result_state = await nodes.verify(url_monitor_state)

        assert result_state["verification_result"].verified is True
        assert result_state["incident"].verification_result.verified is True

    @pytest.mark.asyncio
    async def test_failing_target_url_verifies_false_without_touching_configured_verifier(
        self, monkeypatch, url_monitor_state
    ):
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeUrlAsyncClient(_FakeUrlResponse(500)))

        nodes = _make_orchestrator_nodes(_PoisonVerifier())
        result_state = await nodes.verify(url_monitor_state)

        assert result_state["verification_result"].verified is False

    @pytest.mark.asyncio
    async def test_simulator_incident_still_uses_the_configured_verifier(self, monkeypatch):
        """Control case — proves the branch is source-specific, not a
        blanket bypass: a simulator incident must still reach whatever
        verifier is configured."""
        incident = Incident(service_name="payment-service")
        incident.remediation_request = RemediationRequest(action="restart_service", target_service="payment-service")
        incident.remediation_result = RemediationResult(action="restart_service", success=True)

        class RecordingVerifier:
            called = False

            async def verify(self, incident):
                RecordingVerifier.called = True
                return VerificationResult(verified=True, checks_passed=1, checks_total=1, message="ok")

        nodes = _make_orchestrator_nodes(RecordingVerifier())
        await nodes.verify({"incident": incident})

        assert RecordingVerifier.called is True

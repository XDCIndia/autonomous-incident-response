"""Tests that docker_ctl reaches the orchestrator's VerificationInterface
(issue #6) — via IncidentOrchestrator directly and via the get_orchestrator()
singleton getter.
"""

from __future__ import annotations

import pytest

import backend.orchestrator as orchestrator_module
from backend.orchestrator import IncidentOrchestrator, get_orchestrator


class _Sentinel:
    """Stands in for a DockerController — never touched, identity-checked only."""


@pytest.fixture(autouse=True)
def reset_orchestrator_singleton():
    orchestrator_module._orchestrator = None
    yield
    orchestrator_module._orchestrator = None


class TestIncidentOrchestratorDockerCtl:
    def test_no_docker_ctl_verification_has_none(self):
        orchestrator = IncidentOrchestrator()
        assert orchestrator.verification._docker is None

    def test_docker_ctl_reaches_verification_interface(self):
        sentinel = _Sentinel()
        orchestrator = IncidentOrchestrator(docker_ctl=sentinel)
        assert orchestrator.verification._docker is sentinel

    def test_explicit_verification_injection_overrides_docker_ctl(self):
        from backend.orchestrator.nodes import VerificationInterface

        explicit = VerificationInterface()
        orchestrator = IncidentOrchestrator(docker_ctl=_Sentinel(), verification=explicit)
        assert orchestrator.verification is explicit


class TestGetOrchestratorSingleton:
    def test_first_call_wires_docker_ctl_into_singleton(self):
        sentinel = _Sentinel()
        orchestrator = get_orchestrator(docker_ctl=sentinel)
        assert orchestrator.verification._docker is sentinel

    def test_later_call_does_not_rewire_existing_singleton(self):
        first = get_orchestrator(docker_ctl=_Sentinel())
        second = get_orchestrator(docker_ctl=_Sentinel())
        assert first is second

    def test_no_docker_ctl_defaults_to_none(self):
        orchestrator = get_orchestrator()
        assert orchestrator.verification._docker is None

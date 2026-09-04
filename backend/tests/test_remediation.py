"""Unit tests for remediation engine."""

import pytest
from backend.remediation.actions import (
    AllowedAction,
    RemediationEngine,
    ROOT_CAUSE_ACTION_MAP,
    SEVERITY_AUTONOMY_MAP,
)
from backend.contracts import RemediationRequest, SeverityLevel


class TestRemediationEngine:
    def test_requires_approval_p1(self):
        engine = RemediationEngine()
        assert engine.requires_approval(SeverityLevel.P1) is True

    def test_requires_approval_p2(self):
        engine = RemediationEngine()
        assert engine.requires_approval(SeverityLevel.P2) is True

    def test_autonomous_p3(self):
        engine = RemediationEngine()
        assert engine.requires_approval(SeverityLevel.P3) is False

    def test_autonomous_p4(self):
        engine = RemediationEngine()
        assert engine.requires_approval(SeverityLevel.P4) is False

    def test_recommendation_bad_deployment(self):
        engine = RemediationEngine()
        assert engine.get_recommended_action("bad_deployment") == "rollback_deploy"

    def test_recommendation_database_failure(self):
        engine = RemediationEngine()
        assert engine.get_recommended_action("database_failure") == "reset_connection_pool"

    def test_recommendation_dependency_outage(self):
        engine = RemediationEngine()
        assert engine.get_recommended_action("dependency_outage") == "circuit_break"

    def test_recommendation_resource_exhaustion(self):
        engine = RemediationEngine()
        assert engine.get_recommended_action("resource_exhaustion") == "scale_up"

    @pytest.mark.asyncio
    async def test_execute_rollback(self):
        engine = RemediationEngine()
        request = RemediationRequest(
            action="rollback_deploy",
            target_service="payment-service",
        )
        result = await engine.execute(request)
        assert result.success is True
        assert "v2.4.0" in result.message
        assert engine.get_state()["current_version"] == "v2.4.0"

    @pytest.mark.asyncio
    async def test_execute_restart(self):
        engine = RemediationEngine()
        request = RemediationRequest(
            action="restart_service",
            target_service="inventory-service",
        )
        result = await engine.execute(request)
        assert result.success is True
        assert engine.get_state()["db_connections_used"] == 0

    @pytest.mark.asyncio
    async def test_execute_scale_up(self):
        engine = RemediationEngine()
        request = RemediationRequest(
            action="scale_up",
            target_service="order-service",
        )
        result = await engine.execute(request)
        assert result.success is True
        assert engine.get_state()["scale_count"] == 2

    @pytest.mark.asyncio
    async def test_execute_circuit_break(self):
        engine = RemediationEngine()
        request = RemediationRequest(
            action="circuit_break",
            target_service="external-gateway",
        )
        result = await engine.execute(request)
        assert result.success is True
        assert "external-gateway" in engine.get_state()["circuit_breakers"]

    @pytest.mark.asyncio
    async def test_unknown_action_fails(self):
        engine = RemediationEngine()
        request = RemediationRequest(
            action="unknown_action",
            target_service="test",
        )
        result = await engine.execute(request)
        assert result.success is False

    def test_all_allowed_actions_exist(self):
        for action in AllowedAction:
            assert action.value in ROOT_CAUSE_ACTION_MAP.values() or action.value == "switch_to_secondary"

    def test_all_severities_mapped(self):
        for severity in SeverityLevel:
            assert severity in SEVERITY_AUTONOMY_MAP

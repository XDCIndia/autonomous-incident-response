"""Remediation engine with fixed, allowed actions.

Safety invariant: LLM can ONLY select from this action set.
No arbitrary shell commands. No arbitrary code execution.

Person 3 implements real remediation logic here.
For the foundation, actions modify mock simulator state.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from backend.contracts import RemediationRequest, RemediationResult, SeverityLevel

logger = logging.getLogger(__name__)


class AllowedAction(str, Enum):
    """Fixed set of remediation actions the system can take."""
    ROLLBACK_DEPLOY = "rollback_deploy"
    RESTART_SERVICE = "restart_service"
    SCALE_UP = "scale_up"
    CIRCUIT_BREAK = "circuit_break"
    SWITCH_TO_SECONDARY = "switch_to_secondary"
    RESET_CONNECTION_POOL = "reset_connection_pool"


# Mapping from root cause to recommended action
ROOT_CAUSE_ACTION_MAP: dict[str, AllowedAction] = {
    "bad_deployment": AllowedAction.ROLLBACK_DEPLOY,
    "database_failure": AllowedAction.RESET_CONNECTION_POOL,
    "dependency_outage": AllowedAction.CIRCUIT_BREAK,
    "resource_exhaustion": AllowedAction.SCALE_UP,
    "service_error": AllowedAction.RESTART_SERVICE,
    "unknown": AllowedAction.RESTART_SERVICE,
}

# Severity → autonomy level mapping
SEVERITY_AUTONOMY_MAP: dict[SeverityLevel, bool] = {
    SeverityLevel.P1: True,   # requires approval
    SeverityLevel.P2: True,   # requires approval
    SeverityLevel.P3: False,  # autonomous
    SeverityLevel.P4: False,  # autonomous
}


class RemediationEngine:
    """Executes remediation actions from the fixed allowed set.

    Mock implementation: modifies internal state to simulate remediation.
    """

    def __init__(self):
        self._simulated_state: dict[str, Any] = {
            "current_version": "v2.4.1",
            "db_connections_used": 100,
            "db_connections_max": 100,
            "scale_count": 1,
            "circuit_breakers": {},
        }

    def get_recommended_action(self, root_cause: str) -> str:
        """Map a root cause to the recommended remediation action."""
        action = ROOT_CAUSE_ACTION_MAP.get(root_cause, AllowedAction.RESTART_SERVICE)
        return action.value

    def requires_approval(self, severity: SeverityLevel) -> bool:
        """Determine if remediation requires human approval."""
        return SEVERITY_AUTONOMY_MAP.get(severity, True)

    async def execute(self, request: RemediationRequest) -> RemediationResult:
        """Execute a remediation action.

        This is a MOCK implementation. Actions modify simulated state.
        Person 3 implements real execution here.
        """
        before_state = dict(self._simulated_state)
        action = request.action
        success = True
        message = ""

        if action == "rollback_deploy":
            old_version = self._simulated_state.get("current_version", "unknown")
            self._simulated_state["current_version"] = "v2.4.0"
            message = f"Rolled back {old_version} → v2.4.0"

        elif action == "restart_service":
            self._simulated_state["db_connections_used"] = 0
            message = f"Restarted {request.target_service} — connection pool reset"

        elif action == "scale_up":
            current = self._simulated_state.get("scale_count", 1)
            self._simulated_state["scale_count"] = current + 1
            message = f"Scaled {request.target_service} from {current} to {current + 1} instances"

        elif action == "circuit_break":
            self._simulated_state.setdefault("circuit_breakers", {})
            self._simulated_state["circuit_breakers"][request.target_service] = "open"
            message = f"Circuit breaker opened for {request.target_service}"

        elif action == "switch_to_secondary":
            message = f"Switched {request.target_service} to secondary endpoint"

        elif action == "reset_connection_pool":
            self._simulated_state["db_connections_used"] = 0
            message = f"Reset connection pool for {request.target_service}"

        else:
            success = False
            message = f"Unknown action: {action}"

        after_state = dict(self._simulated_state)
        logger.info("RemediationEngine: action=%s success=%s message=%s", action, success, message)

        return RemediationResult(
            action=action,
            success=success,
            message=message,
            before_state=before_state,
            after_state=after_state,
        )

    def get_state(self) -> dict[str, Any]:
        """Get current simulated state (for testing/debugging)."""
        return dict(self._simulated_state)

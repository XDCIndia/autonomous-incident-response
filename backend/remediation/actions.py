"""Remediation engine with fixed, allowed actions.

Safety invariant: LLM can ONLY select from this action set.
No arbitrary shell commands. No arbitrary code execution.

Real Docker remediation:
    When a ``DockerController`` is available, ``rollback_deploy`` performs
    actual container replacement using the saved container configuration.

Mock mode (no controller):
    Falls back to the original in-memory state mutation.
"""

from __future__ import annotations

import dataclasses
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

from backend.contracts import RemediationRequest, RemediationResult, SeverityLevel

if TYPE_CHECKING:
    from backend.simulator.docker_controller import DockerController

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

    When ``docker_controller`` is provided, actions that support real
    Docker operations will use it.  Otherwise, falls back to mock
    in-memory state mutation for backward compatibility.
    """

    def __init__(self, docker_controller: DockerController | None = None):
        self._docker = docker_controller
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

        Dispatches to real Docker operations when a controller is
        available and the action supports it.  Falls back to mock
        state mutation otherwise.
        """
        action = request.action

        # ── Real Docker rollback ────────────────────────────────────────
        if action == "rollback_deploy" and self._docker is not None:
            return await self._real_rollback(request)

        # ── Real Docker restart ─────────────────────────────────────────
        if action == "restart_service" and self._docker is not None:
            return await self._real_restart(request)

        # ── Mock fallback for all other actions ─────────────────────────
        return await self._mock_execute(request)

    # -----------------------------------------------------------------
    # Real Docker operations
    # -----------------------------------------------------------------

    async def _real_rollback(self, request: RemediationRequest) -> RemediationResult:
        """Rollback a bad deployment using real Docker operations.

        Expects ``request.parameters`` to contain ``previous_config`` —
        the full container configuration saved during fault injection.
        """
        from backend.simulator.docker_controller import ContainerConfig

        service = request.target_service
        params = request.parameters

        # Capture before-state
        before_health = self._docker.check_health(service)
        before_state = {
            "service": service,
            "status": before_health.get("health", "unknown"),
            "version": before_health.get("version", "unknown"),
            "image": before_health.get("image", "unknown"),
        }

        # Extract the saved configuration
        prev_config_dict = params.get("previous_config")
        if not prev_config_dict:
            return RemediationResult(
                action="rollback_deploy",
                success=False,
                message=f"No previous_config provided for {service} — cannot rollback",
                before_state=before_state,
                after_state=before_state,
            )

        try:
            # Reconstruct the ContainerConfig from the serialized dict
            prev_config = ContainerConfig(**prev_config_dict)
        except Exception as exc:
            return RemediationResult(
                action="rollback_deploy",
                success=False,
                message=f"Invalid previous_config: {exc}",
                before_state=before_state,
                after_state=before_state,
            )

        # Step 1: Remove the bad container
        logger.info("Rollback: removing bad deployment for %s", service)
        self._docker.remove_container(service, force=True)

        # Step 2: Deploy the previous known-good version
        logger.info(
            "Rollback: restoring %s to image=%s version=%s",
            service, prev_config.image, prev_config.version,
        )
        # Remove FORCE_UNHEALTHY from the restored environment
        restored_env_overrides = {
            "FORCE_UNHEALTHY": "false",
            "SERVICE_VERSION": prev_config.version,
        }
        container = self._docker.deploy_version(
            prev_config,
            env_overrides=restored_env_overrides,
        )

        if container is None:
            return RemediationResult(
                action="rollback_deploy",
                success=False,
                message=f"Failed to start previous version for {service}",
                before_state=before_state,
                after_state=before_state,
            )

        # Step 3: Wait for the restored container to become healthy
        logger.info("Rollback: waiting for %s to become healthy", service)
        became_healthy = self._docker.wait_for_health(service, retries=15, delay=2.0)

        after_health = self._docker.check_health(service)
        after_state = {
            "service": service,
            "status": after_health.get("health", "unknown"),
            "version": after_health.get("version", "unknown"),
            "image": after_health.get("image", "unknown"),
        }

        if became_healthy:
            message = (
                f"Rolled back {service}: "
                f"{before_state['version']} → {prev_config.version} — healthy"
            )
        else:
            message = (
                f"Rolled back {service}: "
                f"{before_state['version']} → {prev_config.version} — "
                f"health status: {after_health.get('health', 'unknown')}"
            )

        logger.info("Rollback result: success=%s message=%s", became_healthy, message)

        return RemediationResult(
            action="rollback_deploy",
            success=became_healthy,
            message=message,
            before_state=before_state,
            after_state=after_state,
        )

    async def _real_restart(self, request: RemediationRequest) -> RemediationResult:
        """Restart a service container using real Docker operations."""
        service = request.target_service
        before_health = self._docker.check_health(service)
        before_state = {
            "service": service,
            "status": before_health.get("health", "unknown"),
            "version": before_health.get("version", "unknown"),
        }

        success = self._docker.restart_container(service)
        if success:
            self._docker.wait_for_health(service, retries=10, delay=2.0)

        after_health = self._docker.check_health(service)
        after_state = {
            "service": service,
            "status": after_health.get("health", "unknown"),
            "version": after_health.get("version", "unknown"),
        }

        return RemediationResult(
            action="restart_service",
            success=success,
            message=f"Restarted {service}" if success else f"Failed to restart {service}",
            before_state=before_state,
            after_state=after_state,
        )

    # -----------------------------------------------------------------
    # Mock fallback (original behavior, preserved)
    # -----------------------------------------------------------------

    async def _mock_execute(self, request: RemediationRequest) -> RemediationResult:
        """Original mock implementation — modifies simulated state."""
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
        logger.info("RemediationEngine(mock): action=%s success=%s message=%s", action, success, message)

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

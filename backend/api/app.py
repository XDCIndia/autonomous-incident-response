"""FastAPI application — REST + WebSocket endpoints.

Endpoints:
  POST /incidents/trigger           — trigger a new incident
  GET  /incidents                   — list recent incidents
  GET  /incidents/{id}              — get incident details
  GET  /incidents/{id}/timeline     — get incident timeline
  WS   /ws/incidents/{id}           — WebSocket stream of timeline events
  GET  /health                      — health check
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.contracts import Incident, IncidentState, TelemetryEvent, RemediationRequest
from backend.orchestrator import IncidentOrchestrator, get_orchestrator, configure_orchestrator
from backend.platform.config import get_settings
from backend.platform.events import get_event_bus
from backend.platform.knowledge_base import search_similar
from backend.platform.storage import get_storage
from backend.simulator.docker_controller import DockerController
from backend.simulator.toxiproxy_client import ToxiproxyClient
from backend.simulator.health_checker import ServiceHealthVerifier, verify_service_health
from backend.remediation.actions import RemediationEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------
docker_ctl: Optional[DockerController] = None
toxiproxy_ctl: Optional[ToxiproxyClient] = None
# True once the singleton orchestrator has been wired to the real environment.
_real_env_configured: bool = False
_real_env_lock: asyncio.Lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Real-environment wiring
# ---------------------------------------------------------------------------

def _use_service_dns() -> bool:
    """True when the backend runs inside the docker-compose network, where
    services are reachable by DNS name instead of published host ports."""
    return os.environ.get("IRAS_SERVICE_DNS", "").strip().lower() in ("1", "true", "yes", "on")


async def _real_env_available() -> bool:
    """Detect whether the IRAS service stack (payment-service + toxiproxy
    proxies) is actually running so the pipeline can drive it."""
    if docker_ctl is None:
        return False
    try:
        status = await docker_ctl.check_health("payment-service")
        if not status.get("running"):
            return False
        if toxiproxy_ctl is None:
            return False
        # Best-effort self-heal: recreate the standard proxies if Toxiproxy
        # came up late or restarted after the backend booted (issue #17).
        await asyncio.to_thread(toxiproxy_ctl.ensure_default_proxies)
        rpc_proxy = await asyncio.to_thread(toxiproxy_ctl.get_proxy, "payment-rpc-proxy")
        db_proxy = await asyncio.to_thread(toxiproxy_ctl.get_proxy, "payment-db-proxy")
        return rpc_proxy is not None and db_proxy is not None
    except Exception as e:
        logger.warning("Real environment detection failed: %s", e)
        return False


async def _ensure_real_orchestrator() -> bool:
    """Wire the singleton orchestrator to the real Docker/Toxiproxy
    environment when it is available (settings.real_env = auto|on).

    Once wired, /incidents/trigger performs real fault injection, remediation
    and verification. When the environment is not available, the default
    mock orchestrator is kept (unit tests / local runs stay deterministic).
    """
    global _real_env_configured
    if _real_env_configured:
        return True

    async with _real_env_lock:
        if _real_env_configured:
            return True

        mode = get_settings().real_env.strip().lower()
        if mode == "off":
            return False
        if mode == "on":
            available = docker_ctl is not None and toxiproxy_ctl is not None
        else:  # auto
            available = await _real_env_available()
        if not available:
            return False

        existing = get_orchestrator()
        if existing._approval_events:
            # Do not swap the singleton while an approval is pending elsewhere.
            logger.warning("Real environment available but approvals pending — keeping current orchestrator")
            return False

        verifier = (
            ServiceHealthVerifier(docker_ctl, use_service_dns=_use_service_dns())
            if docker_ctl is not None
            else None
        )
        configure_orchestrator(
            remediation_engine=RemediationEngine(
                docker_controller=docker_ctl,
                toxiproxy_client=toxiproxy_ctl,
            ),
            verification=verifier,
        )
        _real_env_configured = True
        logger.info("Orchestrator wired to real environment (real_env=%s)", mode)
        return True


# ---------------------------------------------------------------------------
# Per-incident environment reset (issue #16) + bootstrap (issue #17)
# ---------------------------------------------------------------------------

_TOXIPROXY_SCENARIOS = ("database_failure", "dependency_outage")

# Bounded startup wait for Toxiproxy (issue #17): the proxies must exist
# before scenario endpoints are used, but a purely-local mock/dev backend
# (no stack) must still be able to boot.
TOXIPROXY_STARTUP_GRACE_SECONDS = 40.0


async def _prepare_toxiproxy() -> bool:
    """Restore a clean Toxiproxy baseline and ensure the standard proxies exist.

    Used at startup (with retry), during real-environment detection, and
    before each toxiproxy fault injection so the proxy topology is never
    silently missing (issue #17).  Returns True only when both the reset and
    the proxy ensure succeeded.
    """
    if toxiproxy_ctl is None:
        return False
    reset_ok = await asyncio.to_thread(toxiproxy_ctl.reset)
    proxies_ok = await asyncio.to_thread(toxiproxy_ctl.ensure_default_proxies)
    return reset_ok and proxies_ok


async def _reset_toxiproxy_before_injection(scenario: str) -> None:
    """Restore a clean Toxiproxy baseline before injecting a toxiproxy-backed
    fault, so every incident in one backend process starts from a healthy
    environment (proxies enabled, no stale toxics).

    Without this a previous incident's ``circuit_break`` leaves the RPC proxy
    disabled forever, so later dependency_outage runs never actually degrade
    the service yet are reported and resolved as genuine recoveries.
    """
    if scenario not in _TOXIPROXY_SCENARIOS or toxiproxy_ctl is None:
        return
    if not await _prepare_toxiproxy():
        raise HTTPException(
            status_code=500,
            detail=f"Failed to prepare Toxiproxy before {scenario} injection",
        )


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    global docker_ctl, toxiproxy_ctl
    settings = get_settings()
    storage = get_storage()
    await storage.init_db()
    logger.info("Application started — env=%s", settings.app_env)
    
    mode = settings.real_env.strip().lower()

    try:
        docker_ctl = await asyncio.to_thread(DockerController)
    except Exception as e:
        docker_ctl = None
        logger.warning("Could not initialize DockerController: %s", e)

    # Construct the orchestrator singleton now (rather than lazily on the
    # first request) so its verification stage picks up the real
    # DockerController — a lazy get_orchestrator() call from a later request
    # would only ever see it as None, permanently falling back to the stub.
    get_orchestrator(docker_ctl=docker_ctl)

    # Toxiproxy readiness: the standard proxies must exist before scenario
    # endpoints are used.  On the first `docker compose up` the Toxiproxy
    # image may still be pulling while the backend boots, so retry for a
    # bounded window (issue #17) instead of silently running without proxies
    # for the lifetime of the process.  In REAL_ENV=off (mock/dev) a single
    # best-effort attempt is made so startup is never delayed without infra.
    toxiproxy_ready = False
    try:
        toxiproxy_ctl = await asyncio.to_thread(ToxiproxyClient)
        if mode == "off":
            toxiproxy_ready = await _prepare_toxiproxy()
        else:
            deadline = time.monotonic() + TOXIPROXY_STARTUP_GRACE_SECONDS
            while time.monotonic() < deadline:
                if await _prepare_toxiproxy():
                    toxiproxy_ready = True
                    break
                logger.warning("Toxiproxy not ready yet — retrying...")
                await asyncio.sleep(2.0)
            if not toxiproxy_ready:
                logger.error(
                    "Toxiproxy never became ready within %.0fs — toxiproxy "
                    "scenarios will not apply real faults until it is reachable",
                    TOXIPROXY_STARTUP_GRACE_SECONDS,
                )
    except Exception as e:
        logger.warning("Could not initialize ToxiproxyClient: %s", e)

    # REAL_ENV=on is an explicit demand for the real environment: fail loudly
    # at startup instead of booting into a silently-fabricating mock mode.
    if mode == "on" and (docker_ctl is None or not toxiproxy_ready):
        raise RuntimeError(
            "REAL_ENV=on but the real environment is unavailable "
            f"(docker={'ok' if docker_ctl is not None else 'missing'}, "
            f"toxiproxy={'ok' if toxiproxy_ready else 'unreachable'}). "
            "Start the docker-compose stack, or set REAL_ENV=auto/off."
        )

    # Wire the pipeline to the real environment if the IRAS service stack is
    # already up (compose starts payment-service before the backend).
    try:
        await _ensure_real_orchestrator()
    except Exception as e:
        logger.warning("Could not wire real orchestrator: %s", e)

    yield
    await storage.close()
    logger.info("Application shutdown")


app = FastAPI(
    title="Autonomous Incident Response",
    description="Generic enterprise incident-response system",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class TriggerRequest(BaseModel):
    """Request to trigger an incident."""
    service_name: str = "payment-service"
    target_url: Optional[str] = None
    scenario: str = "bad_deployment"  # bad_deployment | database_failure | dependency_outage | resource_exhaustion
    extra_signals: list[dict] = []


class TriggerResponse(BaseModel):
    """Response after triggering an incident."""
    incident_id: str
    status: str
    message: str


class ApprovalResponse(BaseModel):
    """Response for approval actions."""
    incident_id: str
    status: str
    message: str


class FaultInjectionRequest(BaseModel):
    """Request to inject a fault via API."""
    scenario: str
    service_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "autonomous-incident-response"}


@app.get("/services/health")
async def services_health(service: str = "payment-service"):
    """Check the health of a specific IRAS-managed service."""
    if docker_ctl is None:
        raise HTTPException(status_code=503, detail="Docker controller not available")
    
    status = await docker_ctl.check_health(service)
    return status


@app.post("/faults/inject")
async def inject_fault(request: FaultInjectionRequest):
    """Inject a fault using real Docker operations."""
    if docker_ctl is None:
        raise HTTPException(status_code=503, detail="Docker controller not available")

    from backend.simulator.scenarios import (
        inject_bad_deployment,
        inject_dependency_outage,
        inject_database_failure,
        inject_resource_exhaustion,
    )

    try:
        if request.scenario == "bad_deployment":
            result = await inject_bad_deployment(
                service=request.service_name,
                docker_controller=docker_ctl,
                **request.parameters,
            )
            metadata = {
                "previous_config": result.previous_config,
                "bad_version": result.bad_version,
            }
        elif request.scenario == "resource_exhaustion":
            result = await inject_resource_exhaustion(
                service=request.service_name,
                docker_controller=docker_ctl,
                **request.parameters,
            )
            return {
                "status": "success",
                "message": f"Injected {request.scenario} on {request.service_name}",
                "docker_performed": result.docker_performed,
                "metadata": result.metadata
            }
        elif request.scenario == "dependency_outage":
            if toxiproxy_ctl is None:
                raise HTTPException(status_code=503, detail="Toxiproxy controller not available")
            # Clean baseline first so the fault below genuinely degrades the
            # service even when a previous incident left the proxy disabled.
            await _reset_toxiproxy_before_injection(request.scenario)
            result = await asyncio.to_thread(
                inject_dependency_outage,
                service=request.service_name,
                toxiproxy_client=toxiproxy_ctl,
                **request.parameters,
            )
            metadata = result.metadata
        elif request.scenario == "database_failure":
            if toxiproxy_ctl is None:
                raise HTTPException(status_code=503, detail="Toxiproxy controller not available")
            await _reset_toxiproxy_before_injection(request.scenario)
            result = await asyncio.to_thread(
                inject_database_failure,
                service=request.service_name,
                toxiproxy_client=toxiproxy_ctl,
                **request.parameters,
            )
            metadata = result.metadata
        else:
            raise HTTPException(status_code=400, detail=f"Scenario {request.scenario} not implemented for direct injection")

        # A real injection must actually have degraded the environment — never
        # report success when the fault did not take effect (stale/disabled
        # proxy, missing container, etc.).
        if not result.docker_performed:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Fault injection for {request.scenario} did not take effect on "
                    f"{request.service_name} (container/proxy unavailable or proxy disabled)"
                ),
            )

        return {
            "status": "success",
            "message": f"Injected {request.scenario} on {request.service_name}",
            "docker_performed": result.docker_performed,
            "metadata": metadata,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Fault injection failed for %s/%s: %s", request.service_name, request.scenario, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Fault injection failed: {e}")


@app.post("/remediation/execute")
async def execute_remediation(request: RemediationRequest):
    """Execute a remediation action using real Docker operations."""
    if docker_ctl is None:
        raise HTTPException(status_code=503, detail="Docker controller not available")

    engine = RemediationEngine(
        docker_controller=docker_ctl,
        toxiproxy_client=toxiproxy_ctl
    )
    try:
        result = await engine.execute(request)
    except Exception as e:
        logger.error("Remediation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Remediation failed: {e}")

    # Also run verification if successful — use host-mapped ports for HTTP checks
    verification = None
    if result.success:
        host_port_map = {
            "payment-service": "5001",
            "rpc-service-primary": "5002",
            "rpc-service-secondary": "5003",
            "db-service": "5004",
        }
        port = host_port_map.get(request.target_service, "5000")
        health_url = f"http://localhost:{port}/health"

        verify_urls = []
        if request.target_service == "payment-service" and request.action in ("circuit_break", "switch_to_secondary", "reset_connection_pool"):
            verify_urls.append(f"http://localhost:{port}/pay")

        try:
            verification = await verify_service_health(
                docker_ctl,
                request.target_service,
                health_url=health_url,
                verify_urls=verify_urls
            )
        except Exception as e:
            logger.warning("Verification failed after remediation: %s", e)

    return {
        "status": "success" if result.success else "failure",
        "result": result.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json") if verification else None
    }


@app.post("/incidents/trigger", response_model=TriggerResponse)
async def trigger_incident(request: TriggerRequest):
    """Trigger a new incident with the specified scenario.

    This creates the incident, injects fault signals, and runs the full pipeline.
    """
    from backend.simulator import (
        inject_bad_deployment,
        inject_database_failure,
        inject_dependency_outage,
        inject_resource_exhaustion,
    )

    # Create incident
    incident = Incident(
        service_name=request.service_name,
        target_url=request.target_url,
        state=IncidentState.CREATED,
    )

    # Wire to the real environment when it is available so the fault below is
    # actually injected into the running Docker/Toxiproxy services.  When the
    # real environment is NOT available the whole pipeline stays in mock mode
    # (mock signals + mock remediation) so the two never mix.
    #
    # resource_exhaustion is deliberately excluded from this wiring — its
    # injection stays mock-signal-only here even when the real environment is
    # available (real CPU-exhaustion injection exists for /faults/inject, see
    # backend/simulator/scenarios.py's docker_controller param, but wiring it
    # into the autonomous trigger flow too is a separate follow-up).
    use_real_env = await _ensure_real_orchestrator()

    # Every incident must start from a clean environment: a previous
    # circuit_break may have left the RPC proxy disabled / toxics in place,
    # which would otherwise let this incident "succeed" without ever failing
    # the service (issue #16).
    if use_real_env and request.scenario in _TOXIPROXY_SCENARIOS:
        await _reset_toxiproxy_before_injection(request.scenario)

    # Inject the fault into the real environment (when available) and collect
    # the telemetry that the investigators will reason about.
    #
    # Note: inject_bad_deployment and inject_resource_exhaustion are async
    # (async DockerController); database/dependency use a sync ToxiproxyClient
    # (run via to_thread) and degrade to mock signals when no controller is
    # passed.
    if request.scenario == "bad_deployment":
        result = await inject_bad_deployment(
            service=request.service_name,
            docker_controller=docker_ctl if use_real_env else None,
        )
    elif request.scenario == "database_failure":
        result = await asyncio.to_thread(
            inject_database_failure,
            service=request.service_name,
            toxiproxy_client=toxiproxy_ctl if use_real_env else None,
        )
    elif request.scenario == "dependency_outage":
        result = await asyncio.to_thread(
            inject_dependency_outage,
            service=request.service_name,
            toxiproxy_client=toxiproxy_ctl if use_real_env else None,
        )
    elif request.scenario == "resource_exhaustion":
        result = await inject_resource_exhaustion(service=request.service_name)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario: {request.scenario}. "
                   f"Available: [bad_deployment, database_failure, dependency_outage, resource_exhaustion]",
        )

    # A real-mode fault must genuinely have taken effect — never let the
    # pipeline fabricate an incident on mock signals when the environment was
    # supposed to be driven (issue #16 / #14).
    real_capable_scenarios = ("bad_deployment", "database_failure", "dependency_outage")
    if (
        use_real_env
        and request.scenario in real_capable_scenarios
        and not result.docker_performed
    ):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Failed to inject real {request.scenario} fault on "
                f"{request.service_name} — environment did not degrade"
            ),
        )

    # Extract signals and metadata if the injector returned a FaultInjectionResult
    if hasattr(result, "signals"):
        incident.signals = result.signals
    else:
        incident.signals = result

    # Save initial state
    storage = get_storage()
    await storage.save_incident(incident)

    # Run pipeline in background (use singleton orchestrator for approval support)
    orchestrator = get_orchestrator()
    orchestrator.storage = storage
    orchestrator.event_bus = get_event_bus()

    async def _run():
        try:
            await orchestrator.run_pipeline(incident)
        except Exception as e:
            logger.error("Pipeline error for %s: %s", incident.id, e)

    asyncio.create_task(_run())

    return TriggerResponse(
        incident_id=incident.id,
        status="processing",
        message=f"Incident created — running {request.scenario} scenario for '{request.service_name}'",
    )


@app.get("/incidents")
async def list_incidents(limit: int = Query(50, ge=1, le=100)):
    """List recent incidents."""
    storage = get_storage()
    incidents = await storage.list_incidents(limit=limit)
    return [
        {
            "id": inc.id,
            "service_name": inc.service_name,
            "state": inc.state,
            "severity": inc.severity,
            "current_stage": inc.current_stage,
            "created_at": inc.created_at.isoformat(),
        }
        for inc in incidents
    ]


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: str):
    """Get incident details."""
    storage = get_storage()
    incident = await storage.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident.model_dump(mode="json")


@app.post("/incidents/{incident_id}/approve", response_model=ApprovalResponse)
async def approve_incident(incident_id: str):
    """Approve remediation for a SEMI_AUTONOMOUS incident."""
    storage = get_storage()
    incident = await storage.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    orchestrator = get_orchestrator()
    processed = await orchestrator.approve(incident_id)

    if not processed:
        return ApprovalResponse(
            incident_id=incident_id,
            status="no_pending_approval",
            message="No pending approval for this incident",
        )

    return ApprovalResponse(
        incident_id=incident_id,
        status="approved",
        message="Remediation approved — pipeline will continue",
    )


@app.post("/incidents/{incident_id}/reject", response_model=ApprovalResponse)
async def reject_incident(incident_id: str):
    """Reject remediation for a SEMI_AUTONOMOUS incident."""
    storage = get_storage()
    incident = await storage.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    orchestrator = get_orchestrator()
    processed = await orchestrator.reject(incident_id)

    if not processed:
        return ApprovalResponse(
            incident_id=incident_id,
            status="no_pending_approval",
            message="No pending approval for this incident",
        )

    return ApprovalResponse(
        incident_id=incident_id,
        status="rejected",
        message="Remediation rejected — skipping to report",
    )


@app.get("/knowledge-base/search")
async def search_knowledge_base(query: str = Query(..., min_length=1), top_k: int = Query(3, ge=1, le=10)):
    """Search historical incidents similar to `query` (TF-IDF cosine similarity)."""
    storage = get_storage()
    results = await search_similar(storage, query, top_k=top_k)
    return {"query": query, "results": results}


@app.get("/incidents/{incident_id}/timeline")
async def get_timeline(incident_id: str):
    """Get the timeline for an incident."""
    storage = get_storage()
    incident = await storage.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {
        "incident_id": incident_id,
        "timeline": [e.model_dump(mode="json") for e in incident.timeline],
    }


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.get("/incidents/{incident_id}/approval")
async def get_approval_status(incident_id: str):
    """Check if an incident has a pending approval."""
    orchestrator = get_orchestrator()
    has_pending = incident_id in orchestrator._approval_events
    return {
        "incident_id": incident_id,
        "has_pending_approval": has_pending,
    }


@app.websocket("/ws/incidents/{incident_id}")
async def websocket_incident(websocket: WebSocket, incident_id: str):
    """WebSocket endpoint — streams timeline events as they happen.

    Connect to receive real-time updates during incident processing.
    """
    await websocket.accept()

    event_bus = get_event_bus()
    queue = event_bus.subscribe(incident_id)

    try:
        # Send existing timeline events first
        storage = get_storage()
        incident = await storage.get_incident(incident_id)
        if incident:
            for event in incident.timeline:
                data = event.model_dump(mode="json")
                if "timestamp" in data and hasattr(data["timestamp"], "isoformat"):
                    data["timestamp"] = data["timestamp"].isoformat()
                await websocket.send_json(data)

        # Stream new events
        while True:
            event_data = await event_bus.get_next_event(incident_id, queue, timeout=30.0)
            if event_data is None:
                # Send keepalive
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event_data)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for incident %s", incident_id)
    except Exception as e:
        logger.error("WebSocket error for incident %s: %s", incident_id, e)
    finally:
        event_bus.unsubscribe(incident_id, queue)

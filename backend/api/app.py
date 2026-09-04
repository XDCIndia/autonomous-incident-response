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
from contextlib import asynccontextmanager
from typing import Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.contracts import Incident, IncidentState, TelemetryEvent, RemediationRequest
from backend.orchestrator import IncidentOrchestrator, get_orchestrator
from backend.platform.config import get_settings
from backend.platform.events import get_event_bus
from backend.platform.knowledge_base import search_similar
from backend.platform.storage import get_storage
from backend.simulator.docker_controller import DockerController
from backend.simulator.toxiproxy_client import ToxiproxyClient
from backend.simulator.health_checker import verify_service_health
from backend.remediation.actions import RemediationEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------
docker_ctl: Optional[DockerController] = None
toxiproxy_ctl: Optional[ToxiproxyClient] = None

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
    
    try:
        docker_ctl = await asyncio.to_thread(DockerController)
    except Exception as e:
        logger.warning("Could not initialize DockerController: %s", e)
        
    try:
        toxiproxy_ctl = await asyncio.to_thread(ToxiproxyClient)
        await asyncio.to_thread(toxiproxy_ctl.reset)
        # Pre-create the proxy for the dependency outage scenario
        await asyncio.to_thread(
            toxiproxy_ctl.create_proxy,
            name="payment-rpc-proxy",
            listen="0.0.0.0:8080",
            upstream="rpc-service-primary:5000",
        )
        # Pre-create the proxy for the database failure scenario
        await asyncio.to_thread(
            toxiproxy_ctl.create_proxy,
            name="payment-db-proxy",
            listen="0.0.0.0:8081",
            upstream="db-service:5000",
        )
    except Exception as e:
        logger.warning("Could not initialize ToxiproxyClient: %s", e)
        
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
            return {
                "status": "success",
                "message": f"Injected {request.scenario} on {request.service_name}",
                "docker_performed": result.docker_performed,
                "metadata": {
                    "previous_config": result.previous_config,
                    "bad_version": result.bad_version
                }
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
            result = await asyncio.to_thread(
                inject_dependency_outage,
                service=request.service_name,
                toxiproxy_client=toxiproxy_ctl,
                **request.parameters,
            )
            return {
                "status": "success",
                "message": f"Injected {request.scenario} on {request.service_name}",
                "docker_performed": result.docker_performed,
                "metadata": result.metadata
            }
        elif request.scenario == "database_failure":
            if toxiproxy_ctl is None:
                raise HTTPException(status_code=503, detail="Toxiproxy controller not available")
            result = await asyncio.to_thread(
                inject_database_failure,
                service=request.service_name,
                toxiproxy_client=toxiproxy_ctl,
                **request.parameters,
            )
            return {
                "status": "success",
                "message": f"Injected {request.scenario} on {request.service_name}",
                "docker_performed": result.docker_performed,
                "metadata": result.metadata
            }
        else:
            raise HTTPException(status_code=400, detail=f"Scenario {request.scenario} not implemented for direct injection")
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

    # Inject signals based on scenario
    # Note: inject_bad_deployment and inject_resource_exhaustion are async
    # (use async DockerController); the others are sync (use sync
    # ToxiproxyClient or are pure mock). None of these pass docker_ctl/
    # toxiproxy_ctl here, so this endpoint stays mock-signal-only for all
    # four scenarios — real injection is only wired up via /faults/inject.
    if request.scenario == "bad_deployment":
        result = await inject_bad_deployment(service=request.service_name)
    elif request.scenario == "database_failure":
        result = inject_database_failure(service=request.service_name)
    elif request.scenario == "dependency_outage":
        result = inject_dependency_outage(service=request.service_name)
    elif request.scenario == "resource_exhaustion":
        result = await inject_resource_exhaustion(service=request.service_name)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario: {request.scenario}. "
                   f"Available: [bad_deployment, database_failure, dependency_outage, resource_exhaustion]",
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

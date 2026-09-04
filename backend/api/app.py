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
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.contracts import Incident, IncidentState, TelemetryEvent
from backend.orchestrator import IncidentOrchestrator
from backend.platform.config import get_settings
from backend.platform.events import get_event_bus
from backend.platform.knowledge_base import search_similar
from backend.platform.storage import get_storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    settings = get_settings()
    storage = get_storage()
    await storage.init_db()
    logger.info("Application started — env=%s", settings.app_env)
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


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "autonomous-incident-response"}


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
    scenario_map = {
        "bad_deployment": lambda: inject_bad_deployment(service=request.service_name),
        "database_failure": lambda: inject_database_failure(service=request.service_name),
        "dependency_outage": lambda: inject_dependency_outage(service=request.service_name),
        "resource_exhaustion": lambda: inject_resource_exhaustion(service=request.service_name),
    }

    injector = scenario_map.get(request.scenario)
    if injector is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario: {request.scenario}. "
                   f"Available: {list(scenario_map.keys())}",
        )

    signals = injector()
    incident.signals = signals

    # Save initial state
    storage = get_storage()
    await storage.save_incident(incident)

    # Run pipeline in background
    orchestrator = IncidentOrchestrator(storage=storage, event_bus=get_event_bus())

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

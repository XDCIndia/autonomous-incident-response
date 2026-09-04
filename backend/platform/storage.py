"""SQLite storage for incidents, timeline, and knowledge base.

Person 5 implements real SQLite integration here.
For the foundation, an in-memory implementation is provided.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.contracts import Incident, IncidentState, TimelineEvent

logger = logging.getLogger(__name__)


class Storage:
    """In-memory storage with SQLite-compatible interface.

    Person 5 replaces this with real SQLite/aiosqlite.
    """

    def __init__(self):
        self._incidents: dict[str, dict[str, Any]] = {}
        self._timelines: dict[str, list[dict[str, Any]]] = {}

    async def init_db(self):
        """Initialize database tables."""
        logger.info("Storage: initialized (in-memory)")

    async def save_incident(self, incident: Incident) -> str:
        """Save or update an incident. Returns the incident ID."""
        self._incidents[incident.id] = incident.model_dump(mode="json")
        logger.info("Storage: saved incident %s (state=%s)", incident.id, incident.state)
        return incident.id

    async def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Retrieve an incident by ID."""
        data = self._incidents.get(incident_id)
        if data is None:
            return None
        return Incident.model_validate(data)

    async def list_incidents(self, limit: int = 50) -> list[Incident]:
        """List recent incidents."""
        incidents = []
        for data in sorted(
            self._incidents.values(),
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )[:limit]:
            incidents.append(Incident.model_validate(data))
        return incidents

    async def append_timeline_event(self, incident_id: str, event: TimelineEvent):
        """Append a timeline event to an incident."""
        if incident_id not in self._timelines:
            self._timelines[incident_id] = []
        self._timelines[incident_id].append(event.model_dump(mode="json"))

        # Also update the incident's timeline
        incident = await self.get_incident(incident_id)
        if incident:
            incident.timeline.append(event)
            await self.save_incident(incident)

        logger.info(
            "Storage: timeline event appended to %s — stage=%s status=%s",
            incident_id, event.stage, event.status,
        )

    async def get_timeline(self, incident_id: str) -> list[TimelineEvent]:
        """Retrieve the timeline for an incident."""
        events = self._timelines.get(incident_id, [])
        return [TimelineEvent.model_validate(e) for e in events]

    async def close(self):
        """Close database connections."""
        logger.info("Storage: closed")


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage

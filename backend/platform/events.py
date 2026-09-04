"""Event bus for real-time WebSocket streaming.

Broadcasts timeline events to connected WebSocket clients.
Person 5 implements the real broadcast mechanism here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from backend.contracts import TimelineEvent

logger = logging.getLogger(__name__)


class EventBus:
    """In-process event bus that broadcasts to WebSocket subscribers.

    Each incident gets its own set of subscribers.
    """

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, incident_id: str) -> asyncio.Queue:
        """Subscribe to timeline events for an incident.

        Returns a Queue that receives serialized TimelineEvent dicts.
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(incident_id, []).append(queue)
        logger.info("EventBus: new subscriber for incident %s", incident_id)
        return queue

    def unsubscribe(self, incident_id: str, queue: asyncio.Queue):
        """Remove a subscriber."""
        if incident_id in self._subscribers:
            self._subscribers[incident_id] = [
                q for q in self._subscribers[incident_id] if q is not queue
            ]

    async def publish(self, incident_id: str, event: TimelineEvent):
        """Publish a timeline event to all subscribers of an incident."""
        data = event.model_dump(mode="json")
        # Convert datetime to ISO string for JSON serialization
        if "timestamp" in data and hasattr(data["timestamp"], "isoformat"):
            data["timestamp"] = data["timestamp"].isoformat()

        queues = self._subscribers.get(incident_id, [])
        for queue in queues:
            try:
                await queue.put(data)
            except asyncio.QueueFull:
                logger.warning("EventBus: queue full for incident %s — dropping event", incident_id)

        logger.debug(
            "EventBus: published to %d subscribers for incident %s — stage=%s",
            len(queues), incident_id, event.stage,
        )

    async def get_next_event(self, incident_id: str, queue: asyncio.Queue, timeout: float = 30.0) -> dict | None:
        """Get the next event for a subscriber, with timeout."""
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None


_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus

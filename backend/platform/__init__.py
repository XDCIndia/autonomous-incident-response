"""Platform module — provider-independent infrastructure abstractions.

Person 5 implements the real integrations here.
For the foundation, mock implementations are provided.
"""

from backend.platform.llm_client import LLMClient, get_llm_client
from backend.platform.storage import Storage, get_storage
from backend.platform.config import Settings, get_settings
from backend.platform.events import EventBus, get_event_bus
from backend.platform.knowledge_base import search_similar, index_resolved_incident

__all__ = [
    "LLMClient",
    "get_llm_client",
    "Storage",
    "get_storage",
    "Settings",
    "get_settings",
    "EventBus",
    "get_event_bus",
    "search_similar",
    "index_resolved_incident",
]

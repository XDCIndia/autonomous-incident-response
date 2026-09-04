"""Persistence for user-registered MonitoredTargets.

Lives in its own module sharing Storage's connection, same pattern as
backend/platform/knowledge_base.py's own table — one SQLite file for the
whole app, each concern owning its own schema.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from backend.contracts import MonitoredTarget

if TYPE_CHECKING:
    from backend.platform.storage import Storage

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _init_table(storage: "Storage") -> None:
    conn = storage._require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monitored_targets (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            monitoring_enabled INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()


async def create_target(storage: "Storage", name: str, url: str) -> MonitoredTarget:
    await _init_table(storage)
    conn = storage._require_conn()
    target = MonitoredTarget(name=name, url=url)
    data = target.model_dump(mode="json")
    await conn.execute(
        """
        INSERT INTO monitored_targets (id, url, monitoring_enabled, data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            target.id,
            target.url,
            1 if target.monitoring_enabled else 0,
            json.dumps(data),
            target.created_at.isoformat(),
            target.updated_at.isoformat(),
        ),
    )
    await conn.commit()
    logger.info("MonitoredTarget created: %s (%s)", target.id, target.url)
    return target


async def get_target(storage: "Storage", target_id: str) -> Optional[MonitoredTarget]:
    await _init_table(storage)
    conn = storage._require_conn()
    async with conn.execute(
        "SELECT data FROM monitored_targets WHERE id = ?", (target_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return MonitoredTarget.model_validate(json.loads(row["data"]))


async def list_targets(storage: "Storage") -> list[MonitoredTarget]:
    await _init_table(storage)
    conn = storage._require_conn()
    async with conn.execute(
        "SELECT data FROM monitored_targets ORDER BY created_at DESC"
    ) as cursor:
        rows = await cursor.fetchall()
    return [MonitoredTarget.model_validate(json.loads(row["data"])) for row in rows]


async def list_monitoring_enabled_targets(storage: "Storage") -> list[MonitoredTarget]:
    """Targets the background monitor loop should actually check."""
    await _init_table(storage)
    conn = storage._require_conn()
    async with conn.execute(
        "SELECT data FROM monitored_targets WHERE monitoring_enabled = 1"
    ) as cursor:
        rows = await cursor.fetchall()
    return [MonitoredTarget.model_validate(json.loads(row["data"])) for row in rows]


async def delete_target(storage: "Storage", target_id: str) -> bool:
    await _init_table(storage)
    conn = storage._require_conn()
    cursor = await conn.execute("DELETE FROM monitored_targets WHERE id = ?", (target_id,))
    await conn.commit()
    return cursor.rowcount > 0


async def save_target(storage: "Storage", target: MonitoredTarget) -> None:
    """Persist an updated MonitoredTarget (health check results, toggles, etc.)."""
    await _init_table(storage)
    conn = storage._require_conn()
    target.updated_at = datetime.now(timezone.utc)
    data = target.model_dump(mode="json")
    await conn.execute(
        """
        UPDATE monitored_targets
        SET url = ?, monitoring_enabled = ?, data = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            target.url,
            1 if target.monitoring_enabled else 0,
            json.dumps(data),
            target.updated_at.isoformat(),
            target.id,
        ),
    )
    await conn.commit()


async def set_monitoring_enabled(
    storage: "Storage", target_id: str, enabled: bool
) -> Optional[MonitoredTarget]:
    target = await get_target(storage, target_id)
    if target is None:
        return None
    target.monitoring_enabled = enabled
    await save_target(storage, target)
    return target

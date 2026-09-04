"""SQLite storage for incidents, timeline, and knowledge base.

Real aiosqlite persistence (Person 5 / Platform-Integration).

Two tables:
- incidents         one row per Incident, full state stored as a JSON blob
                    (the Incident model has many nested optional stage
                    results — a JSON blob is far simpler and more robust
                    than a column per nested field, and Pydantic already
                    gives us free validation on the way back out).
- timeline_events   one row per TimelineEvent, hash-chained in insertion
                    order so tampering or dropped events are detectable
                    (see verify_chain()) — this is what makes the
                    "explainable incident timeline" actually trustworthy,
                    not just a log.

A single shared connection is used per Storage instance, serialized with an
asyncio.Lock — aiosqlite connections are not safe for concurrent use from
multiple coroutines, and this app only ever needs one process's worth of
writes at hackathon scale.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Optional

import aiosqlite

from backend.contracts import Incident, IncidentState, TimelineEvent
from backend.platform.config import get_settings

logger = logging.getLogger(__name__)

_GENESIS_HASH = "0" * 64


def _db_path_from_url(database_url: str) -> str:
    """Extract a plain filesystem path from a `sqlite+aiosqlite:///path` URL.

    aiosqlite doesn't understand SQLAlchemy-style URLs directly, so we strip
    the scheme ourselves rather than pulling in SQLAlchemy for this alone.
    """
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if database_url.startswith(prefix):
            return database_url[len(prefix):] or "./incidents.db"
    return database_url or "./incidents.db"


def _event_hash(prev_hash: str, incident_id: str, event: TimelineEvent) -> str:
    """Hash of (previous hash + this event's canonical content).

    Chaining on the previous hash means altering or deleting any past event
    changes every hash after it — the same idea as a git commit chain.
    """
    payload = json.dumps(
        {
            "prev_hash": prev_hash,
            "incident_id": incident_id,
            "id": event.id,
            "timestamp": event.timestamp.isoformat(),
            "stage": event.stage.value,
            "status": event.status,
            "message": event.message,
            "metadata": event.metadata,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Storage:
    """Async SQLite storage for incidents and their timelines."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _db_path_from_url(get_settings().database_url)
        self._conn: aiosqlite.Connection | None = None
        # Guards the read-last-seq + insert-next-seq critical section in
        # append_timeline_event. Without it, two concurrent appends to the
        # SAME incident (e.g. Log + Metric investigators emitting "in
        # parallel", per the plan doc) can both read the same last seq and
        # collide on the UNIQUE(incident_id, seq) constraint.
        self._timeline_lock = asyncio.Lock()

    async def init_db(self):
        """Initialize database tables (idempotent — safe to call every startup)."""
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        # busy_timeout first, before journal_mode=WAL — switching a brand-new
        # file into WAL mode itself needs a moment of exclusive access. In
        # direct testing, setting busy_timeout first reduced but did NOT
        # eliminate a real `sqlite3.OperationalError: database is locked`
        # crash when two Storage instances race to init_db() the same
        # brand-new file for the very first time (a cold multi-worker start,
        # not this app's actual single-instance usage, but cheap to make
        # bulletproof) — so the WAL switch itself also gets a short retry.
        await self._conn.execute("PRAGMA busy_timeout=5000")
        for attempt in range(5):
            try:
                await self._conn.execute("PRAGMA journal_mode=WAL")
                break
            except aiosqlite.OperationalError:
                if attempt == 4:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
        await self._conn.execute("PRAGMA foreign_keys=ON")

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                service_name TEXT NOT NULL,
                state TEXT NOT NULL,
                severity TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at DESC)"
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS timeline_events (
                incident_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                metadata TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                PRIMARY KEY (incident_id, seq),
                FOREIGN KEY (incident_id) REFERENCES incidents(id)
            )
            """
        )
        await self._conn.commit()
        logger.info("Storage: initialized SQLite at %s", self._db_path)

        # Knowledge base seeding lives in its own module but shares this
        # connection so both stay in one file on disk.
        from backend.platform.knowledge_base import seed_knowledge_base

        await seed_knowledge_base(self)

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Storage.init_db() must be called before use")
        return self._conn

    async def save_incident(self, incident: Incident) -> str:
        """Save or update an incident. Returns the incident ID."""
        conn = self._require_conn()
        data = incident.model_dump(mode="json")
        await conn.execute(
            """
            INSERT INTO incidents (id, service_name, state, severity, created_at, updated_at, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                service_name=excluded.service_name,
                state=excluded.state,
                severity=excluded.severity,
                updated_at=excluded.updated_at,
                data=excluded.data
            """,
            (
                incident.id,
                incident.service_name,
                incident.state.value,
                incident.severity.value if incident.severity else None,
                incident.created_at.isoformat(),
                incident.updated_at.isoformat(),
                json.dumps(data),
            ),
        )
        await conn.commit()
        logger.info("Storage: saved incident %s (state=%s)", incident.id, incident.state)

        # Auto-index resolved incidents into the knowledge base — no other
        # role needs to remember to call this.
        if incident.state == IncidentState.RESOLVED and incident.report is not None:
            from backend.platform.knowledge_base import index_resolved_incident

            await index_resolved_incident(self, incident)

        return incident.id

    async def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Retrieve an incident by ID."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT data FROM incidents WHERE id = ?", (incident_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Incident.model_validate(json.loads(row["data"]))

    async def list_incidents(self, limit: int = 50) -> list[Incident]:
        """List recent incidents, newest first.

        ``rowid DESC`` is the tiebreaker for incidents created within the
        same microsecond (``created_at`` is only microsecond-precision), so
        back-to-back saves always come back in insertion order instead of
        in an arbitrary order.
        """
        conn = self._require_conn()
        async with conn.execute(
            "SELECT data FROM incidents ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [Incident.model_validate(json.loads(row["data"])) for row in rows]

    async def append_timeline_event(self, incident_id: str, event: TimelineEvent, _retries_left: int = 5):
        """Append a hash-chained timeline event to an incident.

        The seq-read + seq-insert is a critical section — `_timeline_lock`
        serializes it against other appends from THIS Storage instance, which
        is all a single-process deployment (the app's actual docker-compose
        config: one uvicorn process, no --workers) ever needs.

        That lock provides no protection across separate Storage instances —
        e.g. a second uvicorn worker, or any other process touching the same
        file. Confirmed by direct testing: two instances racing on the same
        incident's timeline can both read the same "last seq" and collide on
        the UNIQUE(incident_id, seq) constraint. Rather than only documenting
        that as a known limitation, retry-on-conflict here makes this method
        correct regardless of how many processes are writing to the file —
        cheap insurance against a config change (e.g. `--workers 2`) turning
        this into a silent dropped-event bug in the middle of a demo.
        """
        conn = self._require_conn()
        collided = False

        async with self._timeline_lock:
            async with conn.execute(
                "SELECT seq, hash FROM timeline_events WHERE incident_id = ? ORDER BY seq DESC LIMIT 1",
                (incident_id,),
            ) as cursor:
                last = await cursor.fetchone()
            seq = (last["seq"] + 1) if last else 0
            prev_hash = last["hash"] if last else _GENESIS_HASH
            this_hash = _event_hash(prev_hash, incident_id, event)

            try:
                await conn.execute(
                    """
                    INSERT INTO timeline_events
                        (incident_id, seq, id, timestamp, stage, status, message, metadata, prev_hash, hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident_id, seq, event.id, event.timestamp.isoformat(),
                        event.stage.value, event.status, event.message,
                        json.dumps(event.metadata), prev_hash, this_hash,
                    ),
                )
                await conn.commit()
            except aiosqlite.IntegrityError:
                if _retries_left <= 0:
                    raise
                collided = True
                logger.warning(
                    "Storage: seq collision on incident %s (another process wrote seq=%d "
                    "concurrently) — recomputing and retrying (%d left)",
                    incident_id, seq, _retries_left,
                )

        if collided:
            # Retry OUTSIDE the lock so the other writer that won the race
            # gets to finish and release it first — recursing while still
            # holding it would just reproduce the exact same collision.
            return await self.append_timeline_event(incident_id, event, _retries_left - 1)

        # Keep the incident row's embedded timeline (used by get_incident) in
        # sync too, so a full incident fetch never needs a second query.
        incident = await self.get_incident(incident_id)
        if incident is not None:
            incident.timeline.append(event)
            await self._save_incident_row_only(incident)

        logger.info(
            "Storage: timeline event appended to %s — stage=%s status=%s",
            incident_id, event.stage, event.status,
        )

    async def _save_incident_row_only(self, incident: Incident) -> None:
        """Update the incidents row without re-triggering KB auto-indexing —
        append_timeline_event already owns the timeline write; this just
        keeps the embedded copy consistent."""
        conn = self._require_conn()
        data = incident.model_dump(mode="json")
        await conn.execute(
            """
            UPDATE incidents SET service_name=?, state=?, severity=?, updated_at=?, data=?
            WHERE id=?
            """,
            (
                incident.service_name,
                incident.state.value,
                incident.severity.value if incident.severity else None,
                incident.updated_at.isoformat(),
                json.dumps(data),
                incident.id,
            ),
        )
        await conn.commit()

    async def get_timeline(self, incident_id: str) -> list[TimelineEvent]:
        """Retrieve the timeline for an incident, in order."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT id, timestamp, stage, status, message, metadata FROM timeline_events "
            "WHERE incident_id = ? ORDER BY seq ASC",
            (incident_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            TimelineEvent(
                id=row["id"],
                timestamp=row["timestamp"],
                stage=row["stage"],
                status=row["status"],
                message=row["message"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    async def verify_chain(self, incident_id: str) -> bool:
        """Recompute the hash chain for an incident's timeline and confirm no
        event was altered, reordered, or dropped since it was written."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT seq, id, timestamp, stage, status, message, metadata, prev_hash, hash "
            "FROM timeline_events WHERE incident_id = ? ORDER BY seq ASC",
            (incident_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        expected_prev = _GENESIS_HASH
        for row in rows:
            event = TimelineEvent(
                id=row["id"],
                timestamp=row["timestamp"],
                stage=row["stage"],
                status=row["status"],
                message=row["message"],
                metadata=json.loads(row["metadata"]),
            )
            if row["prev_hash"] != expected_prev:
                return False
            if _event_hash(row["prev_hash"], incident_id, event) != row["hash"]:
                return False
            expected_prev = row["hash"]
        return True

    async def close(self):
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        logger.info("Storage: closed")


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage

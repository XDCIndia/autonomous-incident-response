"""Tests against REAL file-backed SQLite (not :memory:) — the scenarios
in-memory tests can't exercise: actual WAL file creation, persistence across
a real close+reopen, and — the important one — what happens when two
separate Storage instances (simulating a multi-worker deployment, or a
second tool poking the same file) hit the SAME database file concurrently.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from backend.contracts import Incident, PipelineStage, TimelineEvent
from backend.platform.storage import Storage


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # Storage.init_db() must create it fresh
    yield path
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


class TestRealFilePersistence:
    @pytest.mark.asyncio
    async def test_creates_db_file_on_disk(self, db_path):
        s = Storage(db_path=db_path)
        await s.init_db()
        assert os.path.exists(db_path)
        await s.close()

    @pytest.mark.asyncio
    async def test_wal_mode_actually_enabled(self, db_path):
        s = Storage(db_path=db_path)
        await s.init_db()
        conn = s._require_conn()
        async with conn.execute("PRAGMA journal_mode") as cursor:
            row = await cursor.fetchone()
        assert row[0].lower() == "wal"
        await s.close()

    @pytest.mark.asyncio
    async def test_data_survives_close_and_reopen(self, db_path):
        s1 = Storage(db_path=db_path)
        await s1.init_db()
        incident = Incident(service_name="payment-service")
        await s1.save_incident(incident)
        await s1.append_timeline_event(incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message="hello"))
        await s1.close()

        s2 = Storage(db_path=db_path)
        await s2.init_db()
        fetched = await s2.get_incident(incident.id)
        assert fetched is not None
        assert fetched.service_name == "payment-service"
        timeline = await s2.get_timeline(incident.id)
        assert len(timeline) == 1
        assert await s2.verify_chain(incident.id) is True
        await s2.close()

    @pytest.mark.asyncio
    async def test_reopen_does_not_reseed_knowledge_base(self, db_path):
        """Real bug class to guard against: seeding must check actual row
        count, not an in-process flag — otherwise a second process/instance
        against the same file would re-seed and duplicate the 8 entries."""
        s1 = Storage(db_path=db_path)
        await s1.init_db()
        await s1.close()

        s2 = Storage(db_path=db_path)
        await s2.init_db()
        conn = s2._require_conn()
        async with conn.execute("SELECT COUNT(*) AS n FROM knowledge_base") as cursor:
            row = await cursor.fetchone()
        assert row["n"] == 8  # still 8, not 16
        await s2.close()

    @pytest.mark.asyncio
    async def test_large_number_of_incidents_and_events(self, db_path):
        s = Storage(db_path=db_path)
        await s.init_db()
        for i in range(200):
            incident = Incident(service_name=f"svc-{i}")
            await s.save_incident(incident)
            for j in range(10):
                await s.append_timeline_event(
                    incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message=f"event-{j}")
                )
        incidents = await s.list_incidents(limit=200)
        assert len(incidents) == 200
        sample = incidents[0]
        assert len(await s.get_timeline(sample.id)) == 10
        assert await s.verify_chain(sample.id) is True
        await s.close()


class TestConcurrentColdStart:
    """Regression tests for two real bugs found by directly reproducing a
    concurrent cold start outside pytest (two Storage instances calling
    init_db() at the same time on the same brand-new file — a scenario a
    second uvicorn worker's first boot would hit):

    1. `sqlite3.OperationalError: database is locked` crashed init_db()
       itself, because busy_timeout wasn't set before the journal_mode=WAL
       switch (and even after fixing the ordering, the WAL switch itself
       still needed its own short retry).
    2. seed_knowledge_base()'s old count-then-insert logic produced 16 rows
       instead of 8 when both instances raced past the count check before
       either had inserted.
    """

    @pytest.mark.asyncio
    async def test_concurrent_init_db_on_fresh_file_does_not_crash(self, db_path):
        s1 = Storage(db_path=db_path)
        s2 = Storage(db_path=db_path)
        await asyncio.gather(s1.init_db(), s2.init_db())  # must not raise
        await s1.close()
        await s2.close()

    @pytest.mark.asyncio
    async def test_concurrent_init_db_seeds_exactly_once(self, db_path):
        s1 = Storage(db_path=db_path)
        s2 = Storage(db_path=db_path)
        await asyncio.gather(s1.init_db(), s2.init_db())

        conn = s1._require_conn()
        async with conn.execute("SELECT COUNT(*) AS n FROM knowledge_base") as cursor:
            row = await cursor.fetchone()
        assert row["n"] == 8  # not 16

        await s1.close()
        await s2.close()

    @pytest.mark.asyncio
    async def test_repeated_concurrent_cold_starts_are_all_clean(self):
        """The bugs above were intermittent (timing-dependent), not
        deterministic — one clean run isn't proof. Repeats the race many
        times across fresh files to catch the flaky window."""
        for _ in range(10):
            fd, path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            os.remove(path)
            try:
                s1 = Storage(db_path=path)
                s2 = Storage(db_path=path)
                await asyncio.gather(s1.init_db(), s2.init_db())

                conn = s1._require_conn()
                async with conn.execute("SELECT COUNT(*) AS n FROM knowledge_base") as cursor:
                    row = await cursor.fetchone()
                assert row["n"] == 8

                await s1.close()
                await s2.close()
            finally:
                for suffix in ("", "-wal", "-shm", "-journal"):
                    try:
                        os.remove(path + suffix)
                    except FileNotFoundError:
                        pass


class TestMultiInstanceRealFileConcurrency:
    """Two INDEPENDENT Storage objects (separate asyncio.Lock instances,
    separate aiosqlite connections) pointed at the SAME file — the scenario
    a single-process asyncio.Lock genuinely cannot protect against. This is
    what `uvicorn --workers N > 1` or a second debugging tool touching the
    same incidents.db would actually look like."""

    @pytest.mark.asyncio
    async def test_two_instances_concurrent_incident_saves_no_data_loss(self, db_path):
        s1 = Storage(db_path=db_path)
        s2 = Storage(db_path=db_path)
        await s1.init_db()
        await s2.init_db()

        incidents_1 = [Incident(service_name=f"s1-{i}") for i in range(15)]
        incidents_2 = [Incident(service_name=f"s2-{i}") for i in range(15)]

        await asyncio.gather(
            *[s1.save_incident(i) for i in incidents_1],
            *[s2.save_incident(i) for i in incidents_2],
        )

        all_incidents = await s1.list_incidents(limit=100)
        assert len(all_incidents) == 30

        await s1.close()
        await s2.close()

    @pytest.mark.asyncio
    async def test_two_instances_concurrent_appends_to_same_incident(self, db_path):
        """Regression test for a real bug found via direct (non-pytest)
        reproduction: the per-instance asyncio.Lock only protects appends
        from THAT Storage instance. Two separate instances (a second uvicorn
        worker, in practice) racing on the SAME incident's timeline collided
        on the seq UNIQUE constraint — 2 of 20 appends raised an unhandled
        IntegrityError before the retry-on-conflict fix in
        append_timeline_event(). Now all 20 must land, none dropped, chain intact."""
        s1 = Storage(db_path=db_path)
        await s1.init_db()
        incident = Incident(service_name="shared-incident")
        await s1.save_incident(incident)

        s2 = Storage(db_path=db_path)
        await s2.init_db()

        errors = []

        async def _append(storage, n):
            try:
                await storage.append_timeline_event(
                    incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message=f"from-{n}")
                )
            except Exception as e:
                errors.append(e)

        await asyncio.gather(
            *[_append(s1, f"s1-{i}") for i in range(10)],
            *[_append(s2, f"s2-{i}") for i in range(10)],
        )

        timeline = await s1.get_timeline(incident.id)
        assert errors == []
        assert len(timeline) == 20
        assert len({e.message for e in timeline}) == 20  # none dropped or duplicated
        assert await s1.verify_chain(incident.id) is True

        await s1.close()
        await s2.close()

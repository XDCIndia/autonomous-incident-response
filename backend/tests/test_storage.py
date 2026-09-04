"""Tests for the real aiosqlite-backed Storage — persistence, ordering, and
the hash-chained timeline's tamper detection."""

from __future__ import annotations

import asyncio

import pytest

from backend.contracts import (
    ArbiterResult,
    Incident,
    IncidentState,
    IncidentReport,
    LogInvestigationResult,
    MetricInvestigationResult,
    PipelineStage,
    RemediationResult,
    SeverityLevel,
    SeverityResult,
    TelemetryEvent,
    TimelineEvent,
    VerificationResult,
)
from backend.platform.storage import Storage, get_storage


@pytest.fixture
async def storage():
    s = Storage(db_path=":memory:")
    await s.init_db()
    yield s
    await s.close()


class TestIncidentPersistence:
    @pytest.mark.asyncio
    async def test_save_and_get_roundtrip(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)

        fetched = await storage.get_incident(incident.id)
        assert fetched is not None
        assert fetched.id == incident.id
        assert fetched.service_name == "payment-service"
        assert fetched.state == IncidentState.CREATED

    @pytest.mark.asyncio
    async def test_get_missing_incident_returns_none(self, storage):
        assert await storage.get_incident("does-not-exist") is None

    @pytest.mark.asyncio
    async def test_update_existing_incident(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)

        incident.state = IncidentState.INVESTIGATING
        incident.severity = SeverityLevel.P2
        await storage.save_incident(incident)

        fetched = await storage.get_incident(incident.id)
        assert fetched.state == IncidentState.INVESTIGATING
        assert fetched.severity == SeverityLevel.P2

    @pytest.mark.asyncio
    async def test_severity_none_persists_as_none(self, storage):
        incident = Incident(service_name="payment-service")
        assert incident.severity is None
        await storage.save_incident(incident)
        fetched = await storage.get_incident(incident.id)
        assert fetched.severity is None

    @pytest.mark.asyncio
    async def test_list_incidents_newest_first(self, storage):
        first = Incident(service_name="svc-a")
        await storage.save_incident(first)
        second = Incident(service_name="svc-b")
        await storage.save_incident(second)

        incidents = await storage.list_incidents(limit=10)
        ids = [i.id for i in incidents]
        assert ids.index(second.id) < ids.index(first.id)

    @pytest.mark.asyncio
    async def test_list_incidents_respects_limit(self, storage):
        for i in range(5):
            await storage.save_incident(Incident(service_name=f"svc-{i}"))
        incidents = await storage.list_incidents(limit=2)
        assert len(incidents) == 2

    @pytest.mark.asyncio
    async def test_list_incidents_default_limit(self, storage):
        for i in range(3):
            await storage.save_incident(Incident(service_name=f"svc-{i}"))
        incidents = await storage.list_incidents()
        assert len(incidents) == 3

    @pytest.mark.asyncio
    async def test_list_incidents_empty_db(self, storage):
        assert await storage.list_incidents() == []

    @pytest.mark.asyncio
    async def test_full_incident_with_all_stage_results_roundtrips(self, storage):
        """Every nested Optional stage-result model must survive a full
        JSON serialize -> SQLite -> deserialize round trip intact."""
        incident = Incident(service_name="payment-service", state=IncidentState.RESOLVED)
        incident.signals = [TelemetryEvent(source="payment-service", event_type="error_rate", value=0.5)]
        incident.log_result = LogInvestigationResult(hypothesis="h", confidence=0.9, suggested_root_cause="bad_deployment")
        incident.metric_result = MetricInvestigationResult(hypothesis="h2", confidence=0.8)
        incident.arbiter_result = ArbiterResult(root_cause="bad_deployment", confidence=0.85)
        incident.severity_result = SeverityResult(severity=SeverityLevel.P1, blast_radius=2)
        incident.severity = SeverityLevel.P1
        incident.remediation_result = RemediationResult(action="rollback_deploy", success=True)
        incident.verification_result = VerificationResult(verified=True, checks_passed=3, checks_total=3)
        incident.report = IncidentReport(incident_id=incident.id, service="payment-service", root_cause="bad_deployment")

        await storage.save_incident(incident)
        fetched = await storage.get_incident(incident.id)

        assert fetched.log_result.confidence == 0.9
        assert fetched.metric_result.hypothesis == "h2"
        assert fetched.arbiter_result.root_cause == "bad_deployment"
        assert fetched.severity_result.blast_radius == 2
        assert fetched.remediation_result.success is True
        assert fetched.verification_result.checks_passed == 3
        assert fetched.report.service == "payment-service"
        assert len(fetched.signals) == 1

    @pytest.mark.asyncio
    async def test_unicode_and_special_characters_in_service_name(self, storage):
        incident = Incident(service_name="pañgo-サービス-😀")
        await storage.save_incident(incident)
        fetched = await storage.get_incident(incident.id)
        assert fetched.service_name == "pañgo-サービス-😀"

    @pytest.mark.asyncio
    async def test_sql_injection_like_service_name_is_safe(self, storage):
        malicious = "x'; DROP TABLE incidents; --"
        incident = Incident(service_name=malicious)
        await storage.save_incident(incident)
        fetched = await storage.get_incident(incident.id)
        assert fetched.service_name == malicious
        # Table must still exist and be queryable.
        assert await storage.list_incidents() != []

    @pytest.mark.asyncio
    async def test_concurrent_saves_of_different_incidents(self, storage):
        incidents = [Incident(service_name=f"svc-{i}") for i in range(20)]
        await asyncio.gather(*[storage.save_incident(inc) for inc in incidents])
        all_saved = await storage.list_incidents(limit=100)
        assert len(all_saved) == 20

    @pytest.mark.asyncio
    async def test_concurrent_updates_to_same_incident_all_land(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)

        async def _bump(i):
            copy = await storage.get_incident(incident.id)
            copy.severity = SeverityLevel.P3
            await storage.save_incident(copy)

        await asyncio.gather(*[_bump(i) for i in range(10)])
        fetched = await storage.get_incident(incident.id)
        assert fetched.severity == SeverityLevel.P3  # no crash, no corrupted row


class TestTimeline:
    @pytest.mark.asyncio
    async def test_append_and_get_timeline_in_order(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)

        for stage in (PipelineStage.DETECTION, PipelineStage.INVESTIGATION, PipelineStage.ARBITER):
            await storage.append_timeline_event(
                incident.id,
                TimelineEvent(stage=stage, status="completed", message=f"{stage.value} done"),
            )

        timeline = await storage.get_timeline(incident.id)
        assert [e.stage for e in timeline] == [
            PipelineStage.DETECTION, PipelineStage.INVESTIGATION, PipelineStage.ARBITER,
        ]

    @pytest.mark.asyncio
    async def test_timeline_syncs_into_incident_row(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        await storage.append_timeline_event(
            incident.id,
            TimelineEvent(stage=PipelineStage.DETECTION, status="completed", message="detected"),
        )
        fetched = await storage.get_incident(incident.id)
        assert len(fetched.timeline) == 1
        assert fetched.timeline[0].message == "detected"

    @pytest.mark.asyncio
    async def test_get_timeline_for_incident_with_no_events(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        assert await storage.get_timeline(incident.id) == []

    @pytest.mark.asyncio
    async def test_get_timeline_for_nonexistent_incident(self, storage):
        assert await storage.get_timeline("ghost-incident") == []

    @pytest.mark.asyncio
    async def test_timeline_event_metadata_roundtrips(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        await storage.append_timeline_event(
            incident.id,
            TimelineEvent(
                stage=PipelineStage.SEVERITY, status="completed", message="m",
                metadata={"blast_radius": 3, "services": ["a", "b"], "nested": {"x": 1}},
            ),
        )
        timeline = await storage.get_timeline(incident.id)
        assert timeline[0].metadata == {"blast_radius": 3, "services": ["a", "b"], "nested": {"x": 1}}

    @pytest.mark.asyncio
    async def test_two_incidents_timelines_do_not_cross_contaminate(self, storage):
        a = Incident(service_name="svc-a")
        b = Incident(service_name="svc-b")
        await storage.save_incident(a)
        await storage.save_incident(b)

        await storage.append_timeline_event(a.id, TimelineEvent(stage=PipelineStage.DETECTION, message="a-event"))
        await storage.append_timeline_event(b.id, TimelineEvent(stage=PipelineStage.DETECTION, message="b-event"))

        timeline_a = await storage.get_timeline(a.id)
        timeline_b = await storage.get_timeline(b.id)
        assert [e.message for e in timeline_a] == ["a-event"]
        assert [e.message for e in timeline_b] == ["b-event"]

    @pytest.mark.asyncio
    async def test_many_sequential_events_preserve_order(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        for i in range(50):
            await storage.append_timeline_event(
                incident.id,
                TimelineEvent(stage=PipelineStage.DETECTION, message=f"event-{i}"),
            )
        timeline = await storage.get_timeline(incident.id)
        assert [e.message for e in timeline] == [f"event-{i}" for i in range(50)]

    @pytest.mark.asyncio
    async def test_concurrent_appends_to_same_incident_all_land_without_dropping(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        await asyncio.gather(*[
            storage.append_timeline_event(incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message=f"e{i}"))
            for i in range(15)
        ])
        timeline = await storage.get_timeline(incident.id)
        assert len(timeline) == 15
        assert len({e.message for e in timeline}) == 15  # none dropped/duplicated


class TestHashChain:
    @pytest.mark.asyncio
    async def test_verify_chain_passes_for_untouched_timeline(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        for i in range(3):
            await storage.append_timeline_event(
                incident.id,
                TimelineEvent(stage=PipelineStage.DETECTION, status="completed", message=f"event {i}"),
            )
        assert await storage.verify_chain(incident.id) is True

    @pytest.mark.asyncio
    async def test_verify_chain_empty_timeline_is_valid(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        assert await storage.verify_chain(incident.id) is True

    @pytest.mark.asyncio
    async def test_verify_chain_nonexistent_incident_is_valid(self, storage):
        assert await storage.verify_chain("ghost") is True

    @pytest.mark.asyncio
    async def test_verify_chain_single_event(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        await storage.append_timeline_event(incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message="only one"))
        assert await storage.verify_chain(incident.id) is True

    @pytest.mark.asyncio
    async def test_verify_chain_detects_message_tampering(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        await storage.append_timeline_event(incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message="original"))
        assert await storage.verify_chain(incident.id) is True

        conn = storage._require_conn()
        await conn.execute(
            "UPDATE timeline_events SET message = 'tampered' WHERE incident_id = ?", (incident.id,)
        )
        await conn.commit()
        assert await storage.verify_chain(incident.id) is False

    @pytest.mark.asyncio
    async def test_verify_chain_detects_reordering(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        await storage.append_timeline_event(incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message="first"))
        await storage.append_timeline_event(incident.id, TimelineEvent(stage=PipelineStage.INVESTIGATION, message="second"))

        conn = storage._require_conn()
        # Swap the two rows' content (not their `seq` primary key, which
        # would hit the UNIQUE(incident_id, seq) constraint mid-statement) —
        # this simulates the ledger being reordered/rewritten in place, which
        # the hash chain (bound to each row's original seq position) must catch.
        await conn.execute(
            "UPDATE timeline_events SET message = 'second' WHERE incident_id = ? AND seq = 0", (incident.id,)
        )
        await conn.execute(
            "UPDATE timeline_events SET message = 'first' WHERE incident_id = ? AND seq = 1", (incident.id,)
        )
        await conn.commit()
        assert await storage.verify_chain(incident.id) is False

    @pytest.mark.asyncio
    async def test_verify_chain_detects_deleted_middle_event(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        for i in range(3):
            await storage.append_timeline_event(incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message=f"e{i}"))

        conn = storage._require_conn()
        await conn.execute("DELETE FROM timeline_events WHERE incident_id = ? AND seq = 1", (incident.id,))
        await conn.commit()
        assert await storage.verify_chain(incident.id) is False

    @pytest.mark.asyncio
    async def test_verify_chain_detects_forged_hash(self, storage):
        incident = Incident(service_name="payment-service")
        await storage.save_incident(incident)
        await storage.append_timeline_event(incident.id, TimelineEvent(stage=PipelineStage.DETECTION, message="e"))

        conn = storage._require_conn()
        await conn.execute(
            "UPDATE timeline_events SET hash = 'deadbeef' WHERE incident_id = ?", (incident.id,)
        )
        await conn.commit()
        assert await storage.verify_chain(incident.id) is False

    @pytest.mark.asyncio
    async def test_two_incidents_have_independent_chains(self, storage):
        a = Incident(service_name="svc-a")
        b = Incident(service_name="svc-b")
        await storage.save_incident(a)
        await storage.save_incident(b)
        await storage.append_timeline_event(a.id, TimelineEvent(stage=PipelineStage.DETECTION, message="a"))
        await storage.append_timeline_event(b.id, TimelineEvent(stage=PipelineStage.DETECTION, message="b"))
        assert await storage.verify_chain(a.id) is True
        assert await storage.verify_chain(b.id) is True


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_use_before_init_db_raises(self):
        s = Storage(db_path=":memory:")
        with pytest.raises(RuntimeError):
            await s.get_incident("x")

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, storage):
        await storage.close()
        await storage.close()  # must not raise

    @pytest.mark.asyncio
    async def test_init_db_is_idempotent_when_called_twice_before_writes(self):
        s = Storage(db_path=":memory:")
        await s.init_db()
        await s.init_db()  # re-opens a fresh :memory: connection — fine, nothing written yet
        await s.save_incident(Incident(service_name="x"))
        assert len(await s.list_incidents()) == 1
        await s.close()

    def test_get_storage_singleton_returns_same_instance(self):
        import backend.platform.storage as storage_module

        storage_module._storage = None
        a = get_storage()
        b = get_storage()
        assert a is b
        storage_module._storage = None


class TestKnowledgeBaseAutoIndexing:
    @pytest.mark.asyncio
    async def test_resolved_incident_with_report_is_indexed(self, storage):
        from backend.platform.knowledge_base import search_similar

        incident = Incident(service_name="payment-service", state=IncidentState.RESOLVED)
        incident.report = IncidentReport(
            incident_id=incident.id,
            service="payment-service",
            severity=SeverityLevel.P1,
            root_cause="bad_deployment",
            impact="Deployment v9.9.9 broke payment-service checkout flow entirely.",
        )
        await storage.save_incident(incident)

        results = await search_similar(storage, "payment-service deployment checkout", top_k=5)
        assert any(r["incident_id"] == incident.id for r in results)

    @pytest.mark.asyncio
    async def test_non_resolved_incident_is_not_indexed(self, storage):
        from backend.platform.knowledge_base import search_similar

        incident = Incident(service_name="payment-service", state=IncidentState.INVESTIGATING)
        await storage.save_incident(incident)

        results = await search_similar(storage, "payment-service", top_k=50)
        assert not any(r["incident_id"] == incident.id for r in results)

    @pytest.mark.asyncio
    async def test_resolved_without_report_is_not_indexed(self, storage):
        from backend.platform.knowledge_base import search_similar

        incident = Incident(service_name="payment-service", state=IncidentState.RESOLVED)
        assert incident.report is None
        await storage.save_incident(incident)

        results = await search_similar(storage, "payment-service", top_k=50)
        assert not any(r["incident_id"] == incident.id for r in results)

    @pytest.mark.asyncio
    async def test_saving_resolved_incident_twice_does_not_duplicate_kb_row(self, storage):
        """Regression test: the real orchestrator calls save_incident() twice
        at the Report stage (once inside _emit, once explicitly after) —
        re-indexing on the second call must replace, not duplicate."""
        incident = Incident(service_name="payment-service", state=IncidentState.RESOLVED)
        incident.report = IncidentReport(
            incident_id=incident.id,
            service="payment-service",
            severity=SeverityLevel.P1,
            root_cause="bad_deployment",
            impact="Deployment broke payment-service.",
        )
        await storage.save_incident(incident)
        await storage.save_incident(incident)  # simulates the orchestrator's second save

        conn = storage._require_conn()
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge_base WHERE incident_id = ?", (incident.id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["n"] == 1

    @pytest.mark.asyncio
    async def test_updated_report_replaces_kb_row_content(self, storage):
        incident = Incident(service_name="payment-service", state=IncidentState.RESOLVED)
        incident.report = IncidentReport(incident_id=incident.id, service="payment-service", impact="First impact text.")
        await storage.save_incident(incident)

        incident.report = IncidentReport(incident_id=incident.id, service="payment-service", impact="Updated impact text.")
        await storage.save_incident(incident)

        conn = storage._require_conn()
        async with conn.execute(
            "SELECT description FROM knowledge_base WHERE incident_id = ?", (incident.id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["description"] == "Updated impact text."

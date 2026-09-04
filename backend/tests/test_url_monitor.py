"""Unit tests for real URL health monitoring and deterministic incident
detection (issue #36).

A fake httpx.AsyncClient stands in for real network calls; storage is a real
in-memory Storage instance (matching this repo's established pattern) so
target CRUD and incident persistence behave exactly like production.
"""

from __future__ import annotations

import pytest

import backend.monitoring.url_monitor as url_monitor_module
from backend.contracts import Incident, IncidentState
from backend.monitoring import targets as target_store
from backend.monitoring.url_monitor import TargetMonitor, check_url_health
from backend.platform.storage import Storage


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | None = None, raises: Exception | None = None):
        self._response = response
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *args, **kwargs):
        if self._raises:
            raise self._raises
        return self._response


class FakeOrchestrator:
    """Records every incident it was asked to run. Never resolves them on
    its own — tests call resolve_last() explicitly, at a precise point in
    the test, to simulate "the (real, async, unpredictably-timed) pipeline
    finished in the background" without racing TargetMonitor's own
    fire-and-forget asyncio.create_task scheduling."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.run_calls: list[Incident] = []

    async def run_pipeline(self, incident: Incident):
        self.run_calls.append(incident)

    async def resolve_last(self, state: IncidentState) -> None:
        incident = self.run_calls[-1]
        incident.state = state
        await self.storage.save_incident(incident)


@pytest.fixture
async def storage():
    s = Storage(db_path=":memory:")
    await s.init_db()
    yield s
    await s.close()


class TestCheckUrlHealth:
    @pytest.mark.asyncio
    async def test_2xx_is_success(self, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(200))
        )
        result = await check_url_health("http://example.com")
        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_4xx_is_still_success_reachable(self, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(404))
        )
        result = await check_url_health("http://example.com")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_5xx_is_failure(self, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(503))
        )
        result = await check_url_health("http://example.com")
        assert result["success"] is False
        assert result["status_code"] == 503

    @pytest.mark.asyncio
    async def test_connection_error_is_failure_not_crash(self, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx,
            "AsyncClient",
            lambda *a, **k: _FakeAsyncClient(raises=ConnectionError("refused")),
        )
        result = await check_url_health("http://example.com")
        assert result["success"] is False
        assert result["status_code"] is None
        assert "refused" in result["error"]


class TestTargetMonitorDetection:
    @pytest.mark.asyncio
    async def test_healthy_target_creates_no_incident(self, storage, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(200))
        )
        target = await target_store.create_target(storage, "My App", "http://example.com")
        orchestrator = FakeOrchestrator(storage)
        monitor = TargetMonitor(storage, lambda: orchestrator, failure_threshold=3)

        await monitor.check_once()

        updated = await target_store.get_target(storage, target.id)
        assert updated.health_status == "healthy"
        assert updated.consecutive_failures == 0
        assert orchestrator.run_calls == []

    @pytest.mark.asyncio
    async def test_below_threshold_failures_create_no_incident(self, storage, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(500))
        )
        target = await target_store.create_target(storage, "My App", "http://example.com")
        orchestrator = FakeOrchestrator(storage)
        monitor = TargetMonitor(storage, lambda: orchestrator, failure_threshold=3)

        await monitor.check_once()
        await monitor.check_once()

        updated = await target_store.get_target(storage, target.id)
        assert updated.consecutive_failures == 2
        assert updated.health_status == "unhealthy"
        assert orchestrator.run_calls == []

    @pytest.mark.asyncio
    async def test_threshold_crossed_creates_real_incident_through_orchestrator(self, storage, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(500))
        )
        target = await target_store.create_target(storage, "My App", "http://example.com")
        orchestrator = FakeOrchestrator(storage)
        monitor = TargetMonitor(storage, lambda: orchestrator, failure_threshold=3)

        await monitor.check_once()
        await monitor.check_once()
        await monitor.check_once()

        assert len(orchestrator.run_calls) == 1
        incident = orchestrator.run_calls[0]
        assert incident.source == "url_monitor"
        assert incident.target_url == "http://example.com"
        assert incident.service_name == "My App"
        assert len(incident.signals) > 0
        # Evidence must be real, not fabricated — every signal metadata
        # carries the actual HTTP status observed.
        assert any("500" in s.metadata.get("log_message", "") for s in incident.signals)

        updated = await target_store.get_target(storage, target.id)
        assert updated.active_incident_id == incident.id

    @pytest.mark.asyncio
    async def test_does_not_pile_on_incidents_while_one_is_active(self, storage, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(500))
        )
        target = await target_store.create_target(storage, "My App", "http://example.com")
        # Orchestrator never resolves the incident — it stays "in flight"
        # for the purposes of this test (resolve_last() is never called).
        orchestrator = FakeOrchestrator(storage)
        monitor = TargetMonitor(storage, lambda: orchestrator, failure_threshold=2)

        for _ in range(5):
            await monitor.check_once()

        assert len(orchestrator.run_calls) == 1

    @pytest.mark.asyncio
    async def test_terminal_incident_does_not_rearm_while_still_unhealthy(self, storage, monkeypatch):
        """Reaching a terminal state must NOT re-arm incident creation on
        its own — url_monitor incidents can resolve to terminal in well
        under a second (recommendation-only remediation, single HTTP
        verification), so gating purely on active_incident_id would create
        a new incident on almost every tick for the duration of one real
        outage. Only a genuine recovery (a successful check) re-arms.
        Regression test for the sustained-outage incident flood."""
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(500))
        )
        target = await target_store.create_target(storage, "My App", "http://example.com")
        orchestrator = FakeOrchestrator(storage)
        monitor = TargetMonitor(storage, lambda: orchestrator, failure_threshold=2)

        # First pair of failures crosses the threshold, creates incident #1.
        await monitor.check_once()
        await monitor.check_once()
        assert len(orchestrator.run_calls) == 1

        # Simulate the (real, async) pipeline finishing in the background —
        # terminal, but the URL is still genuinely down.
        await orchestrator.resolve_last(IncidentState.ESCALATED)

        # Many more failed ticks while the outage continues — still exactly
        # one incident, not one per tick.
        for _ in range(10):
            await monitor.check_once()
        assert len(orchestrator.run_calls) == 1

        updated = await target_store.get_target(storage, target.id)
        assert updated.incident_reported is True
        assert updated.active_incident_id is None  # terminal incident cleared, but not re-armed
        assert updated.consecutive_failures >= 2

    @pytest.mark.asyncio
    async def test_new_incident_created_only_after_genuine_recovery_then_new_outage(self, storage, monkeypatch):
        """A SECOND incident is only possible after the target genuinely
        recovers (a successful check resets incident_reported) and then
        fails again — not merely because the first incident went
        terminal."""
        target = await target_store.create_target(storage, "My App", "http://example.com")
        orchestrator = FakeOrchestrator(storage)
        monitor = TargetMonitor(storage, lambda: orchestrator, failure_threshold=2)

        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(500))
        )
        await monitor.check_once()
        await monitor.check_once()
        assert len(orchestrator.run_calls) == 1
        await orchestrator.resolve_last(IncidentState.ESCALATED)

        # Still down for a few more ticks — no second incident yet.
        await monitor.check_once()
        await monitor.check_once()
        assert len(orchestrator.run_calls) == 1

        # Genuine recovery.
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(200))
        )
        await monitor.check_once()
        updated = await target_store.get_target(storage, target.id)
        assert updated.incident_reported is False
        assert updated.health_status == "healthy"
        assert updated.consecutive_failures == 0

        # A brand new outage.
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(500))
        )
        await monitor.check_once()
        await monitor.check_once()
        assert len(orchestrator.run_calls) == 2
        assert orchestrator.run_calls[0].id != orchestrator.run_calls[1].id

    @pytest.mark.asyncio
    async def test_recovery_resets_failure_count(self, storage, monkeypatch):
        target = await target_store.create_target(storage, "My App", "http://example.com")
        orchestrator = FakeOrchestrator(storage)
        monitor = TargetMonitor(storage, lambda: orchestrator, failure_threshold=3)

        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(500))
        )
        await monitor.check_once()
        await monitor.check_once()

        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(200))
        )
        await monitor.check_once()

        updated = await target_store.get_target(storage, target.id)
        assert updated.consecutive_failures == 0
        assert updated.health_status == "healthy"
        assert orchestrator.run_calls == []

    @pytest.mark.asyncio
    async def test_disabled_target_is_never_checked(self, storage, monkeypatch):
        monkeypatch.setattr(
            url_monitor_module.httpx, "AsyncClient", lambda *a, **k: _FakeAsyncClient(_FakeResponse(500))
        )
        target = await target_store.create_target(storage, "My App", "http://example.com")
        await target_store.set_monitoring_enabled(storage, target.id, False)
        orchestrator = FakeOrchestrator(storage)
        monitor = TargetMonitor(storage, lambda: orchestrator, failure_threshold=1)

        await monitor.check_once()

        updated = await target_store.get_target(storage, target.id)
        assert updated.consecutive_failures == 0
        assert updated.health_status == "unknown"
        assert orchestrator.run_calls == []

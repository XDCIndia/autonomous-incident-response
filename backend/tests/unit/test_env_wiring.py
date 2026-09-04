"""Unit tests for wiring the autonomous pipeline to the real environment.

Covers the pieces added to fix issue #14 (autonomous flow driving the real
Docker/Toxiproxy environment):

1. Remediation parameters are collected from injected-signal metadata so the
   real engine (e.g. rollback_deploy needing ``previous_config``) can act.
2. ServiceHealthVerifier checks the real container + HTTP state instead of
   assuming ``remediation_result.success`` means recovery.
3. A full pipeline run with a real RemediationEngine + ServiceHealthVerifier
   injects a real fault, executes the real rollback and only resolves after
   genuine (simulated-but-real) verification passes.

These tests are hermetic: Docker/Toxiproxy calls go through lightweight
fakes, and HTTP checks are stubbed. They do not require a running stack.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.contracts import Incident, TelemetryEvent
from backend.orchestrator.nodes import collect_remediation_parameters
from backend.orchestrator.pipeline import IncidentOrchestrator
from backend.platform.events import EventBus
from backend.platform.storage import Storage
from backend.remediation.actions import RemediationEngine
from backend.simulator.docker_controller import ContainerConfig
from backend.simulator.health_checker import ServiceHealthVerifier
from backend.simulator.scenarios import inject_bad_deployment


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDockerController:
    """Minimal async stand-in for DockerController that records operations."""

    def __init__(self, *, healthy: bool = True, running: bool = True, version: str = "v2.4.0"):
        self.healthy = healthy
        self.running = running
        self.version = version
        self.calls: list[tuple] = []
        self.deployed = 0

    async def check_health(self, service: str) -> dict:
        self.calls.append(("check_health", service))
        return {
            "service": service,
            "running": self.running,
            "health": "healthy" if self.healthy else "unhealthy",
            "version": self.version,
            "container_id": "fake123",
            "image": "payment-image",
        }

    async def save_container_config(self, service: str) -> ContainerConfig:
        self.calls.append(("save_container_config", service))
        return ContainerConfig(
            image="payment-image",
            name=f"iras-{service}",
            service=service,
            version=self.version,
            labels={"iras.service": service},
            environment=["FORCE_UNHEALTHY=false", f"SERVICE_VERSION={self.version}"],
            ports={},
            network="",
            networks=[],
            healthcheck=None,
        )

    async def remove_container(self, service: str, force: bool = True) -> bool:
        self.calls.append(("remove_container", service))
        return True

    async def deploy_version(self, config, *, version_override=None, env_overrides=None):
        self.calls.append(("deploy_version", config.service, version_override))
        self.deployed += 1
        return object()

    async def restart_container(self, service: str, timeout: int = 10) -> bool:
        self.calls.append(("restart_container", service))
        return True

    async def wait_for_health(self, service: str, *, target_health: str = "healthy", retries: int = 15, delay: float = 2.0) -> bool:
        self.calls.append(("wait_for_health", service))
        return self.healthy


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    """Replaces httpx.AsyncClient so HTTP checks are deterministic."""

    def __init__(self, *args, **kwargs):
        self._responses: dict[str, int] = {}

    def set_responses(self, responses: dict[str, int]):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, *args, **kwargs):
        return _FakeResponse(self._responses.get(url, 200))


async def _make_bad_deployment_signals(docker: FakeDockerController) -> list[TelemetryEvent]:
    result = await inject_bad_deployment(
        service="payment-service",
        docker_controller=docker,
    )
    assert result.docker_performed is True  # the fake env was really "changed"
    return result.signals


# ---------------------------------------------------------------------------
# 1. Remediation parameters collected from injected-signal metadata
# ---------------------------------------------------------------------------


class TestRemediationParameterCollection:
    def test_collects_previous_config_and_proxy_keys_from_signals(self):
        incident = Incident(service_name="payment-service")
        incident.signals = [
            TelemetryEvent(
                source="payment-service",
                event_type="deploy",
                metadata={"previous_config": {"image": "x", "version": "v2.4.0"}},
            ),
            TelemetryEvent(
                source="payment-service",
                event_type="error_rate",
                value=0.45,
                metadata={"proxy_name": "payment-db-proxy", "toxic_name": "db_timeout"},
            ),
        ]
        params = collect_remediation_parameters(incident)
        assert params["previous_config"] == {"image": "x", "version": "v2.4.0"}
        assert params["proxy_name"] == "payment-db-proxy"
        assert params["toxic_name"] == "db_timeout"

    def test_empty_when_no_injection_metadata(self):
        incident = Incident(service_name="payment-service")
        incident.signals = [
            TelemetryEvent(source="payment-service", event_type="error_rate", value=0.45),
        ]
        assert collect_remediation_parameters(incident) == {}


# ---------------------------------------------------------------------------
# 2. ServiceHealthVerifier performs real (container + HTTP) checks
# ---------------------------------------------------------------------------


class TestServiceHealthVerifier:
    @pytest.mark.asyncio
    async def test_verified_when_container_and_http_healthy(self, monkeypatch):
        docker = FakeDockerController(healthy=True)
        fake_http = _FakeAsyncClient()
        fake_http.set_responses({
            "http://localhost:5001/health": 200,
            "http://localhost:5001/pay": 200,
        })
        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake_http)

        verifier = ServiceHealthVerifier(docker)
        incident = Incident(service_name="payment-service")
        incident.remediation_request = type(
            "Req", (), {"action": "circuit_break", "target_service": "payment-service"}
        )()

        result = await verifier.verify(incident)
        assert result.verified is True
        assert result.checks_passed == result.checks_total == 3
        assert "check_health" in [c[0] for c in docker.calls]

    @pytest.mark.asyncio
    async def test_not_verified_when_container_not_running(self, monkeypatch):
        docker = FakeDockerController(healthy=False, running=False)
        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _FakeAsyncClient())

        verifier = ServiceHealthVerifier(docker)
        incident = Incident(service_name="payment-service")
        result = await verifier.verify(incident)
        assert result.verified is False


# ---------------------------------------------------------------------------
# 3. Full autonomous pipeline drives the real engine and real verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autonomous_pipeline_drives_real_engine_and_verifier(monkeypatch):
    """Inject real fault → pipeline investigates → real rollback executed →
    real verification passes → incident resolved."""
    docker = FakeDockerController(healthy=True)
    signals = await _make_bad_deployment_signals(docker)
    # The deploy signal must carry the saved config for the real rollback.
    assert any("previous_config" in s.metadata for s in signals)

    fake_http = _FakeAsyncClient()
    fake_http.set_responses({"http://localhost:5001/health": 200})
    monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: fake_http)

    storage = Storage(db_path=":memory:")
    await storage.init_db()
    event_bus = EventBus()
    try:
        orchestrator = IncidentOrchestrator(
            remediation_engine=RemediationEngine(docker_controller=docker),
            verification=ServiceHealthVerifier(docker),
            storage=storage,
            event_bus=event_bus,
        )

        incident = Incident(service_name="payment-service")
        incident.signals = signals

        async def approve_after_delay():
            await asyncio.sleep(0.2)
            await orchestrator.approve(incident.id)

        approve_task = asyncio.create_task(approve_after_delay())
        result = await orchestrator.run_pipeline(incident)
        await approve_task

        assert result.success is True
        # Real rollback was executed against the (fake) Docker controller.
        assert incident.remediation_result is not None
        assert incident.remediation_result.action == "rollback_deploy"
        assert incident.remediation_result.success is True
        assert ("remove_container", "payment-service") in docker.calls
        assert any(c[0] == "deploy_version" for c in docker.calls)
        # Real verification (container + HTTP) ran and the incident resolved.
        assert incident.verification_result is not None
        assert incident.verification_result.verified is True
        assert incident.state.value == "resolved"
    finally:
        await storage.close()


# ---------------------------------------------------------------------------
# Issue #16 — Toxiproxy state must be reset per incident, and injections must
# only report success when a fault genuinely took effect.
# ---------------------------------------------------------------------------


class FakeToxiproxyClient:
    """Sync stand-in for ToxiproxyClient with controllable proxy state."""

    def __init__(self, *, enabled: bool = True, toxics: list[str] | None = None):
        self.enabled = enabled
        self.toxics = list(toxics or [])
        self.reset_calls = 0

    def reset(self) -> bool:
        self.reset_calls += 1
        self.enabled = True
        self.toxics = []
        return True

    def add_toxic(self, proxy_name, toxic_name, toxic_type, toxicity=1.0, attributes=None):
        # Mirror Toxiproxy: a stale toxic with the same name returns a truthy
        # payload via HTTP 409 without applying anything new.
        self.toxics.append(toxic_name)
        return {"name": toxic_name, "type": toxic_type}

    def get_proxy(self, proxy_name: str) -> dict:
        return {
            "name": proxy_name,
            "enabled": self.enabled,
            "toxics": [{"name": t} for t in self.toxics],
        }


def test_toxiproxy_injectors_report_no_fault_when_proxy_disabled():
    """A toxic on a disabled proxy does not degrade the service — injection
    must not claim a real fault happened (the pre-#16 false success)."""
    from backend.simulator.scenarios import inject_database_failure, inject_dependency_outage

    fake = FakeToxiproxyClient(enabled=False, toxics=[])  # e.g. after a circuit_break
    dep = inject_dependency_outage(service="payment-service", toxiproxy_client=fake)
    assert dep.docker_performed is False
    db = inject_database_failure(service="payment-service", toxiproxy_client=fake)
    assert db.docker_performed is False


def test_toxiproxy_injectors_report_fault_when_proxy_enabled():
    from backend.simulator.scenarios import inject_database_failure, inject_dependency_outage

    fake = FakeToxiproxyClient(enabled=True, toxics=[])
    dep = inject_dependency_outage(service="payment-service", toxiproxy_client=fake)
    assert dep.docker_performed is True
    assert dep.metadata["toxic_name"] == "outage_timeout"

    fake2 = FakeToxiproxyClient(enabled=True, toxics=[])
    db = inject_database_failure(service="payment-service", toxiproxy_client=fake2)
    assert db.docker_performed is True
    assert db.metadata["toxic_name"] == "db_timeout"


def test_toxiproxy_reset_reports_success_and_failure():
    """ToxiproxyClient.reset() must tell callers whether the clean baseline
    was actually applied (it previously swallowed errors silently)."""
    from backend.simulator.toxiproxy_client import ToxiproxyClient

    class _SyncResp:
        def raise_for_status(self):
            return None

    class _FakeSyncClient:
        def post(self, url, *args, **kwargs):
            self.last_url = url
            return _SyncResp()

    fake_ok = _FakeSyncClient()
    client = ToxiproxyClient(api_url="http://toxiproxy.invalid:8474")
    client.client = fake_ok  # type: ignore[assignment]
    assert client.reset() is True
    assert fake_ok.last_url.endswith("/reset")

    class _FakeSyncClientFail:
        def post(self, url, *args, **kwargs):
            raise ConnectionError("toxiproxy down")

    client.client = _FakeSyncClientFail()  # type: ignore[assignment]
    assert client.reset() is False


# ---------------------------------------------------------------------------
# Issue #17 — proxies must be (re)created, not assumed to exist from a single
# best-effort startup call.
# ---------------------------------------------------------------------------


def test_ensure_default_proxies_creates_all_standard_proxies():
    from backend.simulator.toxiproxy_client import ToxiproxyClient

    class _RecordingClient(ToxiproxyClient):
        def __init__(self):
            self.created: list[str] = []
            self.fail_next = False

        def create_proxy(self, name, listen, upstream, enabled=True):
            self.created.append(name)
            return None if self.fail_next else {"name": name, "enabled": True}

    client = _RecordingClient()
    assert client.ensure_default_proxies() is True
    # Both proxies in the standard topology are ensured.
    assert client.created == ["payment-rpc-proxy", "payment-db-proxy"]

    # Idempotent on repeat calls (no duplicate creation requests).
    client.created = []
    assert client.ensure_default_proxies() is True
    assert client.created == ["payment-rpc-proxy", "payment-db-proxy"]

    # Reports failure when a proxy cannot be created (Toxiproxy down).
    client.fail_next = True
    assert client.ensure_default_proxies() is False


def test_prepare_toxiproxy_resets_and_ensures_proxies(monkeypatch):
    """The pre-injection helper must both reset toxics AND recreate missing
    proxies, so an injection never targets a proxy that does not exist."""
    import backend.api.app as app_module

    class _Toxiproxy:
        def __init__(self):
            self.reset_calls = 0
            self.ensure_calls = 0

        def reset(self):
            self.reset_calls += 1
            return True

        def ensure_default_proxies(self):
            self.ensure_calls += 1
            return True

    fake = _Toxiproxy()
    monkeypatch.setattr(app_module, "toxiproxy_ctl", fake)

    async def run():
        return await app_module._prepare_toxiproxy()

    assert asyncio.run(run()) is True
    assert fake.reset_calls == 1
    assert fake.ensure_calls == 1

    # When the client is missing entirely the helper reports not-ready.
    monkeypatch.setattr(app_module, "toxiproxy_ctl", None)
    assert asyncio.run(run()) is False


# ---------------------------------------------------------------------------
# Issue #31 — mutating endpoints that drive real Docker/Toxiproxy need an
# API-key gate (when configured) and a non-wildcard CORS allowlist.
# ---------------------------------------------------------------------------


async def _auth_probe(monkeypatch, api_key_value: str, header: str | None) -> int:
    """POST a mutating endpoint through the app and return the status.

    Uses an *unknown* scenario on /incidents/trigger: once the auth gate
    passes, the handler deterministically answers 400 (unknown scenario)
    with no side effects — independent of whether any earlier in-process
    test wired docker/toxiproxy globals.
    """
    from httpx import ASGITransport, AsyncClient

    import backend.api.app as app_module
    from backend.api.app import app
    from backend.platform.config import Settings

    # Bypass the module settings cache so each probe uses the wanted api_key.
    monkeypatch.setattr(
        app_module,
        "get_settings",
        lambda: Settings(_env_file=None, api_key=api_key_value),  # type: ignore[assignment]
    )

    headers = {"X-API-Key": header} if header is not None else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/incidents/trigger",
            json={"service_name": "payment-service", "scenario": "does_not_exist"},
            headers=headers,
        )
        return resp.status_code


@pytest.mark.asyncio
async def test_mutating_endpoints_require_api_key_when_configured(monkeypatch):
    # No header / wrong header -> rejected before the handler runs.
    assert await _auth_probe(monkeypatch, "sekret", None) == 401
    assert await _auth_probe(monkeypatch, "sekret", "wrong") == 401
    # Correct header -> gate passes; handler answers 400 (unknown scenario).
    assert await _auth_probe(monkeypatch, "sekret", "sekret") == 400
    # Auth disabled by default (empty key) -> call reaches the handler.
    assert await _auth_probe(monkeypatch, "", None) == 400


def test_cors_allowlist_defaults_to_dashboard_origins():
    """The CORS allowlist must not be wildcard when real-infra endpoints are
    callable from a browser (issue #31)."""
    from backend.api.app import _allowed_origins
    from backend.platform.config import Settings

    assert "*" not in _allowed_origins
    assert "http://localhost:3000" in _allowed_origins
    # Settings default drives the middleware allowlist.
    defaults = Settings(_env_file=None)
    assert defaults.cors_origins == "http://localhost:3000,http://127.0.0.1:3000"

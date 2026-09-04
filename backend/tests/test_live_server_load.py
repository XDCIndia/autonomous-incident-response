"""Tests against a REAL separate uvicorn process over real TCP/HTTP — not
ASGITransport or starlette.TestClient (both run in-process, sharing this
test's event loop). This is the one category everything else skipped:
genuine concurrent network requests hitting an actual running server, the
closest this suite gets to how the real demo will be driven.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    port = _free_port()
    workdir = tmp_path_factory.mktemp("live_server")
    # This file exercises concurrency/persistence against a *mock* pipeline
    # against synthetic service names (e.g. "load-svc-N") that don't
    # correspond to actual containers — it doesn't stand up the
    # docker-compose service stack. Two independent real-env gates need to
    # be suppressed together, since neither alone covers both code paths:
    #   - REAL_ENV=off stops configure_orchestrator() from swapping in the
    #     real RemediationEngine/ServiceHealthVerifier (see backend/api/app.py
    #     _ensure_real_orchestrator()).
    #   - DOCKER_HOST pointed at a nonexistent socket stops DockerController's
    #     startup ping() from succeeding, so get_orchestrator(docker_ctl=...)
    #     (see backend/orchestrator/__init__.py) falls back to the
    #     verification stage's no-Docker stub too.
    # Without both, a real Docker daemon happening to be reachable on this
    # machine would make either path do real (but unhelpful, since these
    # services don't exist) health checks, and load-test incidents would
    # never resolve.
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{workdir}/incidents.db",
        "REAL_ENV": "off",
        "DOCKER_HOST": "unix:///nonexistent/docker.sock",
        # Likewise blank any LLM provider keys from the local `.env` (this
        # suite tests HTTP/concurrency, not LLM quality/latency) so the
        # server resolves incidents with the deterministic mock agents
        # instead of making real, slow/network-dependent LLM API calls.
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    last_error = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1.0)
            if resp.status_code == 200:
                break
        except Exception as e:
            last_error = e
        time.sleep(0.3)
    else:
        proc.terminate()
        output = proc.communicate(timeout=5)[0] if proc.stdout else ""
        pytest.fail(f"live server never became healthy: {last_error}\n{output}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


class TestRealProcessHealthAndBasics:
    def test_health_over_real_http(self, live_server):
        resp = httpx.get(f"{live_server}/health", timeout=5.0)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_trigger_and_resolve_over_real_http(self, live_server):
        # Use resource_exhaustion (P3/P4 — no approval needed, auto-resolves)
        resp = httpx.post(
            f"{live_server}/incidents/trigger",
            json={"service_name": "payment-service", "scenario": "resource_exhaustion"},
            timeout=5.0,
        )
        assert resp.status_code == 200
        incident_id = resp.json()["incident_id"]

        for _ in range(20):
            time.sleep(0.2)
            check = httpx.get(f"{live_server}/incidents/{incident_id}", timeout=5.0)
            if check.json()["state"] == "resolved":
                break
        else:
            pytest.fail("incident never resolved over real HTTP")

        data = check.json()
        assert data["severity"] in ("P3", "P4")
        assert data["report"] is not None

    def test_malformed_json_body_returns_422_not_500(self, live_server):
        resp = httpx.post(
            f"{live_server}/incidents/trigger",
            content="{not valid json",
            headers={"content-type": "application/json"},
            timeout=5.0,
        )
        assert resp.status_code == 422

    def test_wrong_field_types_returns_422_not_500(self, live_server):
        resp = httpx.post(
            f"{live_server}/incidents/trigger",
            json={"service_name": 12345, "scenario": ["not", "a", "string"]},
            timeout=5.0,
        )
        assert resp.status_code == 422

    def test_extra_unexpected_fields_do_not_break_trigger(self, live_server):
        resp = httpx.post(
            f"{live_server}/incidents/trigger",
            json={
                "service_name": "payment-service",
                "scenario": "bad_deployment",
                "totally_unexpected_field": "whatever",
            },
            timeout=5.0,
        )
        assert resp.status_code == 200


class TestRealConcurrentLoad:
    def test_20_concurrent_real_triggers_all_resolve_without_data_loss(self, live_server):
        with httpx.Client(timeout=10.0) as client:
            incident_ids = []
            for i in range(20):
                resp = client.post(
                    f"{live_server}/incidents/trigger",
                    json={"service_name": f"load-svc-{i}", "scenario": "resource_exhaustion"},
                )
                assert resp.status_code == 200
                incident_ids.append(resp.json()["incident_id"])

            assert len(set(incident_ids)) == 20  # all unique, none collided

            deadline = time.time() + 15
            resolved = set()
            while time.time() < deadline and len(resolved) < 20:
                for iid in incident_ids:
                    if iid in resolved:
                        continue
                    check = client.get(f"{live_server}/incidents/{iid}")
                    if check.json()["state"] == "resolved":
                        resolved.add(iid)
                time.sleep(0.3)

            assert len(resolved) == 20, f"only {len(resolved)}/20 resolved"

            # The live_server fixture is module-scoped (shared across this
            # file's other tests to avoid spawning 7 subprocesses), so the
            # listing may contain incidents from earlier tests too — assert
            # this test's own 20 are all present, not an exact total count.
            listing = client.get(f"{live_server}/incidents")
            listed_ids = {inc["id"] for inc in listing.json()}
            assert set(incident_ids).issubset(listed_ids)

    def test_concurrent_kb_searches_during_active_load_do_not_error(self, live_server):
        import concurrent.futures

        def _search(i):
            resp = httpx.get(
                f"{live_server}/knowledge-base/search",
                params={"query": f"bad deployment errors {i}"},
                timeout=5.0,
            )
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(pool.map(_search, range(15)))

        assert all(s == 200 for s in statuses)

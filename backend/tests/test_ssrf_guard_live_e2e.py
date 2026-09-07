"""Real-network integration tests for SSRF protection (Phase 1 security).

Unlike test_ssrf_guard.py, these make GENUINE outbound HTTP calls — no
mocking of httpx/httpcore/DNS — because SSRF protection is exactly the kind
of network behavior a fully-mocked suite could pass while the real wiring
is broken (private DNS resolution + connection pinning only actually proves
itself against a real resolver and a real socket connect).

Same convention as the repo's existing *_e2e.py tests: requires outbound
internet access and is intentionally excluded from the default hermetic
suite invocation (`pytest backend/tests/unit backend/tests/test_storage.py`,
per README) — run this file directly to validate the real behavior.
"""

from __future__ import annotations

import httpx
import pytest

from backend.monitoring.ssrf_guard import SSRFValidationError, build_safe_transport


@pytest.mark.asyncio
async def test_real_public_url_succeeds_through_safe_transport():
    async with httpx.AsyncClient(transport=build_safe_transport(), timeout=8.0) as client:
        resp = await client.get("https://example.com")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_real_loopback_is_blocked():
    async with httpx.AsyncClient(transport=build_safe_transport(), timeout=5.0) as client:
        with pytest.raises(SSRFValidationError):
            await client.get("http://127.0.0.1:9/")


@pytest.mark.asyncio
async def test_real_redirect_to_metadata_ip_is_blocked():
    """A genuine external redirector pointing at the real cloud-metadata
    address — proves redirects can't bypass validation, using an actual
    redirect response from a live third party, not a simulated one."""
    async with httpx.AsyncClient(
        transport=build_safe_transport(), timeout=8.0, follow_redirects=True
    ) as client:
        with pytest.raises(SSRFValidationError):
            await client.get(
                "https://httpbin.org/redirect-to?url=http://169.254.169.254/latest/meta-data"
            )


@pytest.mark.asyncio
async def test_real_redirect_to_public_host_still_works():
    async with httpx.AsyncClient(
        transport=build_safe_transport(), timeout=8.0, follow_redirects=True
    ) as client:
        resp = await client.get("https://httpbin.org/redirect-to?url=https://example.com")
    assert resp.status_code == 200
    assert str(resp.url).rstrip("/") == "https://example.com"

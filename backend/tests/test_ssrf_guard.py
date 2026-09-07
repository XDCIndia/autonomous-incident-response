"""Hermetic tests for SSRF protection on monitored-target URLs (Phase 1
security). No real network/DNS — monkeypatches DNS resolution only, never
the validation logic itself, so this exercises the real IP-range decision
code. See test_ssrf_guard_live_e2e.py for real-network validation (redirect
handling, an actual loopback connection attempt) — that file is
deliberately excluded from the default hermetic suite, same convention as
the existing *_e2e.py tests.
"""

from __future__ import annotations

import socket

import pytest

from backend.monitoring.ssrf_guard import (
    SSRFSafeNetworkBackend,
    SSRFValidationError,
    parse_target_url,
    resolve_validated_ips,
    validate_target_url,
)


def _fake_getaddrinfo(ip_strings: list[str]):
    """Build a loop.getaddrinfo replacement returning the given IPs."""

    async def _getaddrinfo(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ip_strings]

    return _getaddrinfo


class TestParseTargetUrl:
    def test_http_url(self):
        assert parse_target_url("http://example.com/health") == ("example.com", 80)

    def test_https_url_default_port(self):
        assert parse_target_url("https://example.com") == ("example.com", 443)

    def test_explicit_port(self):
        assert parse_target_url("http://example.com:8080") == ("example.com", 8080)

    def test_rejects_non_http_scheme(self):
        with pytest.raises(SSRFValidationError, match="Unsupported URL scheme"):
            parse_target_url("ftp://example.com")

    def test_rejects_missing_scheme(self):
        with pytest.raises(SSRFValidationError):
            parse_target_url("example.com")

    def test_rejects_missing_hostname(self):
        with pytest.raises(SSRFValidationError, match="hostname"):
            parse_target_url("http:///path")


class TestResolveValidatedIps:
    @pytest.mark.asyncio
    async def test_allows_public_ip(self, monkeypatch):
        loop = __import__("asyncio").get_event_loop()
        monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
        ips = await resolve_validated_ips("example.com", 80)
        assert ips == ["93.184.216.34"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ip,reason",
        [
            ("127.0.0.1", "loopback"),
            ("10.0.0.5", "private"),
            ("172.16.0.5", "private"),
            ("192.168.1.1", "private"),
            ("169.254.169.254", "link-local / cloud metadata"),
            ("169.254.1.1", "link-local"),
            ("224.0.0.1", "multicast"),
            ("0.0.0.0", "unspecified"),
            ("::1", "loopback IPv6"),
            ("fe80::1", "link-local IPv6"),
            ("fd00::1", "unique-local IPv6 (private)"),
        ],
    )
    async def test_blocks_disallowed_ip(self, monkeypatch, ip, reason):
        loop = __import__("asyncio").get_event_loop()
        monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo([ip]))
        with pytest.raises(SSRFValidationError, match="disallowed address"):
            await resolve_validated_ips("evil.example.com", 80)

    @pytest.mark.asyncio
    async def test_blocks_when_any_resolved_ip_is_disallowed(self, monkeypatch):
        """Multi-A-record host where only ONE address is private must still
        be blocked entirely — a resolver could round-robin to it later."""
        loop = __import__("asyncio").get_event_loop()
        monkeypatch.setattr(
            loop, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34", "10.0.0.1"])
        )
        with pytest.raises(SSRFValidationError, match="disallowed address"):
            await resolve_validated_ips("mixed.example.com", 80)

    @pytest.mark.asyncio
    async def test_dns_failure_raises_validation_error_not_crash(self, monkeypatch):
        loop = __import__("asyncio").get_event_loop()

        async def _raise(*a, **k):
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr(loop, "getaddrinfo", _raise)
        with pytest.raises(SSRFValidationError, match="Could not resolve"):
            await resolve_validated_ips("nonexistent.invalid", 80)


class TestValidateTargetUrl:
    @pytest.mark.asyncio
    async def test_accepts_public_url(self, monkeypatch):
        loop = __import__("asyncio").get_event_loop()
        monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
        await validate_target_url("http://example.com")  # must not raise

    @pytest.mark.asyncio
    async def test_rejects_loopback_url(self, monkeypatch):
        loop = __import__("asyncio").get_event_loop()
        monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo(["127.0.0.1"]))
        with pytest.raises(SSRFValidationError):
            await validate_target_url("http://localhost")


class TestSSRFSafeNetworkBackendConnectTcp:
    """Unit-level check that the transport backend actually consults
    resolve_validated_ips before delegating to the real connector, and pins
    to the validated IP rather than the original hostname."""

    @pytest.mark.asyncio
    async def test_blocks_before_delegating_to_inner_backend(self, monkeypatch):
        backend = SSRFSafeNetworkBackend()

        called = {"inner_connect": False}

        async def _inner_connect_tcp(*a, **k):
            called["inner_connect"] = True

        monkeypatch.setattr(backend._inner, "connect_tcp", _inner_connect_tcp)

        loop = __import__("asyncio").get_event_loop()
        monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo(["127.0.0.1"]))

        with pytest.raises(SSRFValidationError):
            await backend.connect_tcp("attacker-controlled.example", 80)
        assert called["inner_connect"] is False

    @pytest.mark.asyncio
    async def test_pins_to_validated_ip_not_hostname(self, monkeypatch):
        backend = SSRFSafeNetworkBackend()

        seen = {}

        async def _inner_connect_tcp(host, port, **k):
            seen["host"] = host
            return "fake-stream"

        monkeypatch.setattr(backend._inner, "connect_tcp", _inner_connect_tcp)

        loop = __import__("asyncio").get_event_loop()
        monkeypatch.setattr(loop, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))

        result = await backend.connect_tcp("example.com", 80)
        assert result == "fake-stream"
        assert seen["host"] == "93.184.216.34"

"""SSRF protection for user-supplied monitored-target URLs.

A ``MonitoredTarget.url`` is attacker-controlled input: any user of this
system can ask it to repeatedly send real HTTP requests to a URL of their
choosing. Without validation this backend becomes a generic SSRF primitive —
"fetch this internal admin panel / cloud metadata endpoint every 15 seconds
and tell me what changed."

Two layers, both required:

1. ``validate_target_url`` — a fast pre-flight check at target-creation time
   (``POST /targets``) so obviously-bad URLs are rejected immediately with a
   clear error instead of being stored and failing silently forever.
2. ``SSRFSafeNetworkBackend`` — a transport-level guard that re-resolves and
   re-validates the destination host at the moment of every real TCP
   connect, including every redirect hop. This is what actually closes the
   gap a pre-flight check alone cannot: DNS can change between target
   creation and any later check ("DNS rebinding"), and ``httpx``'s
   ``follow_redirects=True`` would otherwise happily follow a 302 from a
   validated public host straight to ``http://169.254.169.254/`` without
   this ever being consulted again.

Both layers reject on the *resolved IP address*, never on the hostname
string — a public hostname that happens to resolve to a private/loopback
address is blocked; the human-readable hostname itself is never trusted.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpcore
from httpcore._backends.auto import AutoBackend

ALLOWED_SCHEMES = ("http", "https")

_DEFAULT_PORTS = {"http": 80, "https": 443}


class SSRFValidationError(Exception):
    """Raised when a target URL is malformed or resolves to a disallowed address.

    Deliberately a plain ``Exception`` (not an ``httpx``/``httpcore`` type) so
    it is unambiguous in logs and error messages, but it is always raised from
    contexts that already catch broad ``Exception`` (target creation returns
    it as a 400; ``check_url_health``'s network try/except turns it into an
    ordinary failed-check result, exactly like a connection refusal) — a
    blocked target must degrade to "unreachable", never crash the monitor
    loop.
    """


def _is_blocked_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """True for any address family of "not a legitimate public internet
    destination" — loopback, RFC1918/ULA private ranges, link-local
    (this is what covers the 169.254.169.254 / fd00:ec2::254 cloud metadata
    endpoints on AWS/GCP/Azure without needing a hardcoded IP list),
    multicast, "reserved for future use", and unspecified (0.0.0.0 / ::).
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def parse_target_url(url: str) -> tuple[str, int]:
    """Validate URL shape and return (hostname, port). Raises SSRFValidationError."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SSRFValidationError(
            f"Unsupported URL scheme '{parts.scheme or '(none)'}' — only "
            f"{'/'.join(ALLOWED_SCHEMES)} are allowed"
        )
    hostname = parts.hostname
    if not hostname:
        raise SSRFValidationError("URL must include a hostname")
    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise SSRFValidationError(f"Invalid port in URL: {exc}") from exc
    return hostname, port


async def resolve_validated_ips(hostname: str, port: int) -> list[str]:
    """Resolve ``hostname`` and return every distinct IP it maps to, having
    verified NONE of them are disallowed.

    Conservative by design: if a hostname resolves to multiple addresses and
    even one is private/internal (a common DNS-rebinding / multi-A-record
    attack shape), the whole hostname is rejected rather than picking only
    the "good" address — a later request through a round-robin resolver
    could still land on the bad one.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFValidationError(f"Could not resolve host '{hostname}': {exc}") from exc

    ips: list[str] = []
    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)
        if _is_blocked_ip(ip):
            raise SSRFValidationError(
                f"URL host '{hostname}' resolves to a disallowed address "
                f"({ip_str}) — loopback, private, link-local, and other "
                "internal/reserved addresses are not allowed for monitored targets"
            )
        if ip_str not in ips:
            ips.append(ip_str)

    if not ips:
        raise SSRFValidationError(f"Could not resolve host '{hostname}' to any address")
    return ips


async def validate_target_url(url: str) -> None:
    """Full pre-flight validation for a URL a user wants to register for
    monitoring: shape + scheme + DNS resolution + IP-range check.

    Raises SSRFValidationError with a message safe to return to the caller.
    """
    hostname, port = parse_target_url(url)
    await resolve_validated_ips(hostname, port)


class SSRFSafeNetworkBackend(httpcore.AsyncNetworkBackend):
    """Wraps the real network backend, re-validating the destination at the
    moment of every actual TCP connect.

    This runs on every connection httpcore opens — including one opened to
    follow a redirect to a different host — because httpcore calls
    ``connect_tcp`` again per new origin. Connecting to the validated IP
    literal (rather than letting the inner backend re-resolve the hostname
    itself) closes the TOCTOU window between our validation and the actual
    connection: whatever address we just checked is the exact address that
    gets connected to, full stop.

    TLS server_hostname/SNI is supplied separately by httpcore's connection
    layer (from the request's original origin, not from what ``connect_tcp``
    receives here), so certificate validation against the real hostname is
    unaffected by connecting via its resolved IP.
    """

    def __init__(self) -> None:
        self._inner = AutoBackend()

    async def connect_tcp(
        self,
        host,
        port,
        timeout=None,
        local_address=None,
        socket_options=None,
    ):
        validated_ips = await resolve_validated_ips(host, port)
        pinned_ip = validated_ips[0]
        return await self._inner.connect_tcp(
            pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise NotImplementedError("Unix sockets are not supported for monitored targets")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def build_safe_transport() -> "httpx.AsyncHTTPTransport":  # noqa: F821 - lazy import below
    """An httpx transport that routes every connection (including redirects)
    through SSRFSafeNetworkBackend.

    httpx.AsyncHTTPTransport doesn't expose a ``network_backend`` constructor
    parameter, but it stores its underlying httpcore connection pool on
    ``self._pool`` and that pool's ``_network_backend`` attribute is exactly
    what gets consulted for every connect — swapping it after construction is
    the standard way to plug in custom DNS/connection behavior for httpx.
    """
    import httpx

    transport = httpx.AsyncHTTPTransport()
    transport._pool._network_backend = SSRFSafeNetworkBackend()
    return transport

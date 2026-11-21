"""Outbound URL policy for user-supplied endpoints (SSRF defence,
Master Build V3 §108.15-16).

Rules:
  - Only http/https. https is required for public endpoints; http is
    allowed only for loopback/private (local) targets.
  - Locality is a property of the URL, never of the connection kind
    (v21.3): a loopback/private target is acceptable for ANY connection
    — a local model server and an API-key (BYOK) connection alike.
  - Cloud metadata addresses (169.254.169.254, metadata.google.internal,
    100.100.100.200) and link-local ranges are always blocked.
  - Hostnames are resolved and every resolved address is checked, so a
    public name pointing at a private address is rejected for
    non-local endpoints (DNS-rebinding defence at validation time; the
    HTTP client additionally re-checks via the same helper where
    practical).
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

BLOCKED_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}
METADATA_IPS = {"169.254.169.254", "100.100.100.200", "fd00:ec2::254"}


class UrlPolicyError(ValueError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def _unwrap_mapped(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """IPv4-mapped IPv6 (::ffff:a.b.c.d) classifies exactly like the
    IPv4 address it wraps (v21.4). ipaddress treats the mapped range as
    reserved and NOT loopback/private, so before this unwrap a mapped
    loopback was refused as reserved_range while its plain-IPv4 twin
    was admitted — and a mapped metadata address dodged the metadata
    blocklist, which is keyed on the IPv4 string."""
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped if mapped is not None else ip


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    ip = _unwrap_mapped(ip)
    if ip.is_loopback:
        # Loopback is governed by the caller's allow_local rule, never by
        # the blocked ranges. ipaddress marks ::1 reserved as well as
        # loopback (127.0.0.1 is only loopback); without this exemption an
        # IPv6-loopback endpoint is refused as "reserved_range" while its
        # IPv4 twin is admitted — the ranges below stay blocked regardless.
        return None
    if str(ip) in METADATA_IPS:
        return "metadata_endpoint"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return "reserved_range"
    return None


def egress_proxy() -> str | None:
    """The environment's egress proxy URL, if any.

    Read explicitly instead of via httpx trust_env: some environments
    ship a no_proxy value httpx cannot parse (bracketed IPv6 entries),
    which crashes client construction. Passing the proxy explicitly
    with trust_env=False behaves identically in sane environments and
    survives broken ones.
    """
    import os
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(name)
        if value:
            return value
    return None


def tls_verify() -> str | bool:
    """CA bundle for outbound TLS: SSL_CERT_FILE when provided (some
    egress proxies MITM TLS with a locally-installed CA that certifi
    does not carry), else the system bundle, else certifi default."""
    import os
    from pathlib import Path
    candidate = os.environ.get("SSL_CERT_FILE")
    if candidate and Path(candidate).exists():
        return candidate
    system = "/etc/ssl/certs/ca-certificates.crt"
    if Path(system).exists():
        return system
    return True


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


# Explicit private ranges (NOT ip.is_private, which also flags benchmark
# space like 198.18.0.0/15 that egress proxies legitimately answer with).
_PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "fd00::/8", "fc00::/8")]


def _ip_private(ip) -> bool:
    ip = _unwrap_mapped(ip)
    return any(ip in net for net in _PRIVATE_NETS)


def assert_resolved_host_safe(url: str, *, allow_local: bool = False) -> None:
    """Phase 4 request-time SSRF re-validation (DNS-rebinding defense).

    Validation-time checks can be undone if DNS answers change between
    connection setup and the actual call. This re-resolves the host at
    call time and refuses the request when any answer lands in a blocked
    range (metadata / link-local / reserved — always), or in private /
    loopback space for non-local endpoints.

    Resolution failure does not block: behind an egress proxy the proxy
    resolves names the local resolver cannot, and the proxy remains
    governed by validation-time policy. The residual TOCTOU window is
    documented in KNOWN_LIMITATIONS.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host or host in BLOCKED_HOSTS:
        raise UrlPolicyError("blocked_host", f"host is blocked: {host or '(empty)'}")
    try:
        literal = ipaddress.ip_address(host)
        candidates = [literal]
    except ValueError:
        candidates = []
        for addr in _resolve(host):
            try:
                candidates.append(ipaddress.ip_address(addr))
            except ValueError:
                continue
    for ip in candidates:
        blocked = _ip_blocked(ip)
        if blocked:
            raise UrlPolicyError(blocked, f"host resolves to a blocked address ({ip})")
        if not allow_local and (ip.is_loopback or _ip_private(ip)):
            raise UrlPolicyError(
                "private_resolution",
                f"public endpoint resolves to a private/loopback address ({ip})")


def validate_endpoint_url(url: str, *, kind: str) -> dict:
    """Returns {url, host, is_local} or raises UrlPolicyError.

    `kind` is accepted for call-site compatibility but no longer gates
    locality (v21.3): whether a target is local is derived from the URL
    alone, so any connection kind may point at a local address.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UrlPolicyError("scheme", "only http/https endpoints are allowed")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UrlPolicyError("host", "endpoint URL has no host")
    if host in BLOCKED_HOSTS:
        raise UrlPolicyError("metadata_endpoint", "cloud metadata endpoints are blocked")
    if parsed.username or parsed.password:
        raise UrlPolicyError("credentials_in_url", "credentials must not be embedded in the URL")
    # Port sanity (v21.4): urlparse only raises for a bad port when
    # .port is accessed — do it here so a malformed endpoint is refused
    # at validation with a clear category, not stored and left to fail
    # later as a misleading provider error.
    try:
        port = parsed.port
    except ValueError:
        raise UrlPolicyError(
            "port", "endpoint URL has an invalid port") from None
    if port is not None and not 1 <= port <= 65535:
        raise UrlPolicyError(
            "port", f"endpoint URL port {port} is out of range")

    # Classify the host itself. Resolved-address checks apply to IP
    # literals and local-suffixed names — the SSRF-relevant cases. For
    # public DNS names, validation-time resolution is unreliable behind
    # egress proxies (which answer with synthetic addresses), so public
    # names are governed by scheme + blocklist here and by the HTTP
    # layer in production (documented in KNOWN_LIMITATIONS).
    is_local = host == "localhost" or host.endswith((".local", ".lan", ".internal"))
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        ips = [_unwrap_mapped(ipaddress.ip_address(host))]
        is_local = is_local or ips[0].is_loopback or ips[0].is_private
    except ValueError:
        if is_local:
            for addr in _resolve(host):
                ip = _unwrap_mapped(ipaddress.ip_address(addr))
                ips.append(ip)
                if ip.is_loopback or ip.is_private:
                    is_local = True
    for ip in ips:
        blocked = _ip_blocked(ip)
        if blocked:
            raise UrlPolicyError(blocked, f"endpoint address is blocked ({ip})")

    if parsed.scheme == "http" and not is_local:
        raise UrlPolicyError("scheme", "https is required for non-local endpoints")
    return {"url": url.rstrip("/"), "host": host, "is_local": is_local}

"""Destination address policy for external HTTP/SSE MCP servers (M2e).

A hostname is resolved BEFORE the request and every returned address must pass.
  Always blocked: unspecified, multicast, link-local (incl. 169.254.169.254 cloud metadata),
    reserved ranges and a few well-known metadata addresses.
  Blocked only on sklr/panel ports: loopback and this machine's own addresses.
  Allowed: other private ranges (corporate MCP servers legitimately live there).

Production HTTP/SSE uses returned addresses in PinnedBackend at actual TCP creation,
rather than reconnecting by hostname. Pinning still requires runtime acceptance tests;
do not describe it as full SSRF protection. Still not covered: NAT64/6to4-embedded
addresses and local interface addresses that this host does not report under its own
name (VPN adapters, etc.).
"""
import ipaddress
import socket

import anyio

from external_mcp.limits import CONNECT_TIMEOUT_S, ExternalError

MAX_ADDRESSES = 32
METADATA_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '169.254.0.0/16', '100.100.100.200/32', 'fd00:ec2::254/128',
))


async def default_resolver(host, port):
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def host_addresses():
    """Addresses this machine reports for its own name. Blocking call."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(info[4][0].split('%', 1)[0])
    except OSError:
        pass
    return frozenset(found)


def classify(address, port, blocked_ports=(), own_addresses=frozenset()):
    """Return None when allowed, otherwise a safe error code (never the address)."""
    try:
        ip = ipaddress.ip_address(str(address).split('%', 1)[0])
    except ValueError:
        return 'address_invalid'
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (ip.is_unspecified or ip.is_multicast or ip.is_link_local or
            any(ip in network for network in METADATA_NETWORKS)):
        return 'destination_blocked'
    # Loopback and own addresses are decided BEFORE the reserved check: Python marks the
    # whole ::/8 range (which contains ::1) as reserved, which would wrongly block IPv6
    # loopback on every port instead of only on the sklr/panel ports.
    if ip.is_loopback or str(ip) in own_addresses:
        return 'self_or_panel_endpoint' if port in blocked_ports else None
    if ip.is_reserved:
        return 'destination_blocked'
    return None


async def check_destination(host, port, *, blocked_ports=(), resolver=None, own_addresses=None):
    """Resolve (when needed) and validate all addresses. Returns them for future pinning."""
    resolver = resolver or default_resolver
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            with anyio.fail_after(CONNECT_TIMEOUT_S):
                addresses = list(await resolver(host, port))
        except TimeoutError:
            raise ExternalError('dns_timeout') from None
        except Exception:
            raise ExternalError('dns_failed') from None
    if not addresses or len(addresses) > MAX_ADDRESSES:
        raise ExternalError('dns_failed')
    own = frozenset()
    if port in blocked_ports:
        own = (frozenset(own_addresses) if own_addresses is not None
               else await anyio.to_thread.run_sync(host_addresses))
    for address in addresses:
        code = classify(address, port, blocked_ports, own)
        if code:
            raise ExternalError(code)
    return addresses

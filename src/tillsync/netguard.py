"""SSRF protection for URLs that come from users (a pasted sheet link, a POS base URL).

By default only the public internet is reachable: loopback, private networks, link-local
(including cloud metadata at ``169.254.169.254``), multicast and reserved ranges are refused.
The check runs on every request a guarded client sends, so redirects are checked too.

Pass ``allow_private=True`` to reach loopback and private networks (a POS server on the shop's
LAN, or a test server on localhost). Link-local stays blocked unless ``allow_link_local=True``.

Limitation: the host is resolved for the check and again by the HTTP client, so a hostile DNS
server could answer differently the second time (DNS rebinding). Run untrusted fetches from a
network that can't reach internal services if that matters to you.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

__all__ = ["BlockedURLError", "check_url", "guarded_client"]

Resolver = Callable[[str], list[str]]


class BlockedURLError(ValueError):
    """The URL points somewhere this process must not fetch."""


def _system_resolver(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]").split("%")[0])
    except ValueError:
        return None


def _check_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_private: bool, allow_link_local: bool
) -> None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_link_local:
        if not allow_link_local:
            raise BlockedURLError(f"{ip} is a link-local address")
        return
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise BlockedURLError(f"{ip} is not a routable address")
    if ip.is_global:
        return
    if not allow_private:
        raise BlockedURLError(f"{ip} is not on the public internet")


def check_url(
    url: str | httpx.URL,
    *,
    allow_private: bool = False,
    allow_link_local: bool = False,
    resolver: Resolver | None = None,
) -> None:
    """Raise ``BlockedURLError`` unless ``url`` is http(s) and all its addresses are allowed."""
    parts = urlsplit(str(url))
    if parts.scheme not in ("http", "https"):
        raise BlockedURLError("only http and https URLs can be fetched")
    host = parts.hostname
    if not host:
        raise BlockedURLError("the URL has no host")
    ip = _as_ip(host)
    if ip is not None:
        addresses = [ip]
    else:
        try:
            raw = (resolver or _system_resolver)(host)
        except (socket.gaierror, UnicodeError) as exc:
            raise BlockedURLError(f"could not resolve {host}") from exc
        addresses = [a for a in (_as_ip(r) for r in raw) if a is not None]
        if not addresses:
            raise BlockedURLError(f"could not resolve {host}")
    for addr in addresses:
        _check_ip(addr, allow_private, allow_link_local)


def guarded_client(
    *,
    allow_private: bool = False,
    allow_link_local: bool = False,
    resolver: Resolver | None = None,
    **kwargs: Any,
) -> httpx.Client:
    """An ``httpx.Client`` that refuses blocked destinations on every request and redirect hop.

    Extra keyword arguments go to ``httpx.Client``.
    """

    def _hook(request: httpx.Request) -> None:
        check_url(
            request.url, allow_private=allow_private, allow_link_local=allow_link_local, resolver=resolver
        )

    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [_hook, *hooks.get("request", [])]
    return httpx.Client(event_hooks=hooks, **kwargs)

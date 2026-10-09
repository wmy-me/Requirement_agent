"""Fail-closed validation for URLs sent to an external page reader."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str, int | None], object]


def validate_public_web_url(url: str, *, resolver: Resolver | None = None) -> str:
    """Return a validated URL or raise ``ValueError`` without attempting a fetch.

    DNS failures are unsafe because accepting an unresolved hostname would let a
    later resolver choose a private address.  Callers must re-run this check for
    every redirect target before following it; this MCP layer never follows page
    redirects itself.
    """

    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must use http or https")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must contain a hostname without credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL port is invalid") from exc
    try:
        literal_address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        addresses = [literal_address]
    else:
        resolve = resolver or _resolve
        try:
            addresses = _addresses(resolve(parsed.hostname, port))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("URL hostname could not be safely resolved") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("URL must resolve only to public addresses")
    return url.strip()


def _resolve(hostname: str, port: int | None) -> object:
    return socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)


def _addresses(resolved: object) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for item in resolved:  # type: ignore[union-attr]
        candidate = item[4][0] if isinstance(item, tuple) else item
        addresses.append(ipaddress.ip_address(candidate))
    return addresses


__all__ = ["Resolver", "validate_public_web_url"]

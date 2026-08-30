"""An httpx transport that connects only to an address it just validated.

Validating an api_base when it is saved settles what the name pointed at then.
It says nothing about where it points when the request actually goes out, and
the registry entry is long-lived: an attacker can save a hostname that resolves
somewhere harmless, wait for it to be accepted, and repoint the record
afterwards. No race is needed for that -- the check and the fetch are days
apart.

This closes both that and the narrow rebind window, by resolving once,
validating every answer, and then connecting to the specific address that
passed rather than to the name. The hostname is preserved for the Host header
and for TLS, so certificate verification and virtual hosting still work
against the real name.
"""

from __future__ import annotations

import ipaddress
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.core.net import UnsafeEndpointError, pick_safe_address

logger = get_logger(__name__)


class PinnedResolutionTransport(httpx.AsyncHTTPTransport):
    """Resolve, validate, then connect to the validated address itself."""

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        host = request.url.host

        # A literal address was already checked by validate_api_base and has no
        # name to re-resolve; rewriting it would be a no-op.
        if _is_ip_literal(host):
            return await super().handle_async_request(request)

        address = await pick_safe_address(host)
        if address is None:
            # Unresolvable. Let the connection fail on its own terms rather
            # than inventing an error: nothing is reachable either way, and the
            # transport's own message will be the accurate one.
            return await super().handle_async_request(request)

        original_host = request.headers.get("Host") or _authority(request.url)
        request.url = request.url.copy_with(host=address)
        request.headers["Host"] = original_host
        # Without this the TLS handshake would use the bare IP for SNI and
        # certificate verification, and every HTTPS provider would fail.
        request.extensions = {**request.extensions, "sni_hostname": host}

        logger.debug(
            "outbound_request_pinned",
            extra={"host": host, "address": address},
        )
        return await super().handle_async_request(request)


def _is_ip_literal(host: Optional[str]) -> bool:
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _authority(url: httpx.URL) -> str:
    """host:port as the origin server should see it, omitting a default port."""
    default = 443 if url.scheme == "https" else 80
    if url.port and url.port != default:
        return f"{url.host}:{url.port}"
    return url.host


def build_pinned_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=PinnedResolutionTransport())


__all__ = ["PinnedResolutionTransport", "build_pinned_client", "UnsafeEndpointError"]

"""Keeping a configurable endpoint from becoming an SSRF primitive.

Any application that lets an operator, a tenant or a config file choose where
a model call goes has handed out a server-side fetch. Point it at
169.254.169.254 and cloud credentials come back inside the product; point it at
an internal service and its response does.

Two checks, because one is not enough:

  * `validate_endpoint` runs when a value is accepted. It rejects non-HTTP
    schemes, embedded credentials, metadata addresses and private literals.
  * `PinnedResolutionTransport` runs when the request goes out. It resolves the
    hostname, validates every answer, and connects to the address it just
    checked rather than to the name.

The second is not paranoia about a millisecond race. A configured endpoint
outlives its validation by days, so the practical attack needs no race at all:
supply a name that resolves somewhere harmless, wait for it to be accepted,
then repoint the DNS record.

Local inference legitimately needs private addresses, so they are permitted by
explicit name through the allowlist rather than blocked outright.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Iterable, Optional
from urllib.parse import urlparse

import httpx

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud instance-metadata endpoints. Never reachable, even if a matching name
# were added to an allowlist by mistake.
BLOCKED_ADDRESSES = frozenset(
    {"169.254.169.254", "fd00:ec2::254", "100.100.100.200"}
)

DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "::1", "ollama", "vllm", "host.docker.internal"}
)


class UnsafeEndpointError(ValueError):
    """Raised when an endpoint points somewhere it must not."""


def _is_private(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve(host: str) -> list[str]:
    """Every address `host` currently resolves to, across both families.

    An unresolvable name returns empty rather than raising: a config may name
    an endpoint whose DNS is not live yet, and refusing to accept it would be a
    worse failure than letting the eventual call fail. Nothing is admitted by
    this -- a name that resolves to nothing reaches nothing.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return [info[4][0] for info in infos]


def validate_endpoint(
    url: str,
    *,
    allowlist: Iterable[str] = DEFAULT_ALLOWLIST,
    resolve: bool = True,
) -> str:
    """Return `url` if it is safe to call, else raise UnsafeEndpointError.

    `resolve=False` skips the DNS step and runs only the checks that cost
    nothing. getaddrinfo blocks the calling thread, so pass False anywhere this
    runs on an event loop -- a request handler, a pydantic validator -- and use
    `validate_endpoint_async` instead.
    """
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeEndpointError(
            f"endpoint must use http or https, got '{parsed.scheme or 'none'}'"
        )
    if parsed.username or parsed.password:
        raise UnsafeEndpointError("endpoint must not embed credentials")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeEndpointError("endpoint must include a host")
    if host in BLOCKED_ADDRESSES:
        raise UnsafeEndpointError(f"'{host}' is an instance-metadata address")

    permitted = {entry.strip().lower() for entry in allowlist}
    if host in permitted:
        return url

    if _is_private(host):
        raise UnsafeEndpointError(
            f"'{host}' is a private address. Add it to the allowlist to permit "
            "it deliberately."
        )
    if not resolve:
        return url

    # Checking the literal spelling only stops the obvious attempt. Any name
    # the attacker controls can point inward -- localtest.me resolves to
    # 127.0.0.1 today -- so the name is resolved and every answer checked.
    for address in _resolve(host):
        _reject_if_internal(host, address)
    return url


async def validate_endpoint_async(
    url: str, *, allowlist: Iterable[str] = DEFAULT_ALLOWLIST
) -> str:
    """validate_endpoint with the DNS lookup moved off the event loop."""
    validate_endpoint(url, allowlist=allowlist, resolve=False)
    return await asyncio.to_thread(
        validate_endpoint, url, allowlist=allowlist, resolve=True
    )


def _reject_if_internal(host: str, address: str) -> None:
    if address in BLOCKED_ADDRESSES:
        raise UnsafeEndpointError(
            f"'{host}' resolves to {address}, an instance-metadata address"
        )
    if _is_private(address):
        raise UnsafeEndpointError(
            f"'{host}' resolves to the private address {address}"
        )


async def pick_safe_address(
    host: str, *, allowlist: Iterable[str] = DEFAULT_ALLOWLIST
) -> Optional[str]:
    """Resolve `host` and return one address that passed validation.

    Returns the address rather than a verdict so the caller can connect to the
    exact one it checked. Answering "the name is fine" and then handing the
    name onward is what leaves the rebind window open: the socket's own lookup
    can return something the check never saw.

    None means either "allowlisted, do not pin" or "did not resolve"; both mean
    the request should proceed unmodified.
    """
    if host in BLOCKED_ADDRESSES:
        raise UnsafeEndpointError(f"'{host}' is an instance-metadata address")
    if host.strip().lower() in {entry.strip().lower() for entry in allowlist}:
        # Allowlisted precisely because it is private -- pinning it would break
        # the local runtimes the allowlist exists to permit.
        return None

    addresses = await asyncio.to_thread(_resolve, host)
    if not addresses:
        return None
    for address in addresses:
        _reject_if_internal(host, address)
    return addresses[0]


class PinnedResolutionTransport(httpx.AsyncHTTPTransport):
    """Connect only to an address this transport just validated."""

    def __init__(
        self, *, allowlist: Iterable[str] = DEFAULT_ALLOWLIST, **kwargs: object
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._allowlist = frozenset(entry.strip().lower() for entry in allowlist)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if _is_ip_literal(host):
            # Already settled by validate_endpoint, and no name to re-resolve.
            return await super().handle_async_request(request)

        address = await pick_safe_address(host, allowlist=self._allowlist)
        if address is None:
            return await super().handle_async_request(request)

        original_host = request.headers.get("Host") or _authority(request.url)
        request.url = request.url.copy_with(host=address)
        request.headers["Host"] = original_host
        # Without this the handshake presents the bare IP for SNI and
        # certificate verification, and every HTTPS provider fails.
        request.extensions = {**request.extensions, "sni_hostname": host}
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


def build_pinned_client(
    *, allowlist: Iterable[str] = DEFAULT_ALLOWLIST
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=PinnedResolutionTransport(allowlist=allowlist)
    )

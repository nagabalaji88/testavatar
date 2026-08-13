"""Outbound endpoint policy.

A provider's `api_base` is written through the admin model registry and then
fetched server-side by LiteLLM, which makes it a server-side request forgery
primitive: point it at 169.254.169.254 or an internal service and the response
comes back inside the product.

Local inference legitimately needs private addresses, so private hosts are
permitted by explicit name rather than blocked outright.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from app.core.config import settings

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud instance-metadata addresses. Never reachable, even if a matching host
# name were added to the allowlist by mistake.
BLOCKED_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})


class UnsafeEndpointError(ValueError):
    """Raised when an api_base points somewhere it must not."""


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


def validate_api_base(url: str) -> str:
    """Return the URL if it is safe to call, else raise UnsafeEndpointError."""
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeEndpointError(
            f"api_base must use http or https, got '{parsed.scheme or 'none'}'"
        )
    if parsed.username or parsed.password:
        raise UnsafeEndpointError("api_base must not embed credentials")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeEndpointError("api_base must include a host")
    if host in BLOCKED_ADDRESSES:
        raise UnsafeEndpointError(f"'{host}' is an instance-metadata address")

    allowlist = {entry.strip().lower() for entry in settings.api_base_allowlist}
    if host in allowlist:
        return url

    if _is_private(host):
        raise UnsafeEndpointError(
            f"'{host}' is a private address. Add it to API_BASE_ALLOWLIST to "
            "permit it deliberately."
        )

    return url

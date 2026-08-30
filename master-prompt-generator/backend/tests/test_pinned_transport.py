"""Outbound connections go to the address that was validated.

Validating api_base at save time settles where the name pointed then. A
registry entry outlives that by days, so a name accepted while it pointed
somewhere harmless can be repointed afterwards -- no race required. Resolving
at connect time and then connecting to the name would still leave the rebind
window, because the socket's own lookup can return what the check never saw.
So the checked address is what gets connected to.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
import pytest

from app.core.net import UnsafeEndpointError, pick_safe_address
from app.core.pinned_transport import PinnedResolutionTransport


class TestPickSafeAddress:
    @pytest.mark.asyncio
    async def test_returns_the_address_it_validated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.core.net._resolve", lambda _h: ["93.184.216.34"])
        assert await pick_safe_address("example.com") == "93.184.216.34"

    @pytest.mark.asyncio
    async def test_raises_when_a_name_answers_with_a_private_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.core.net._resolve", lambda _h: ["127.0.0.1"])
        with pytest.raises(UnsafeEndpointError, match="private address"):
            await pick_safe_address("attacker.example.com")

    @pytest.mark.asyncio
    async def test_raises_on_a_metadata_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.core.net._resolve", lambda _h: ["169.254.169.254"])
        with pytest.raises(UnsafeEndpointError, match="instance-metadata"):
            await pick_safe_address("attacker.example.com")

    @pytest.mark.asyncio
    async def test_one_bad_answer_condemns_the_whole_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The socket could have been handed either address."""
        monkeypatch.setattr(
            "app.core.net._resolve", lambda _h: ["93.184.216.34", "10.0.0.7"]
        )
        with pytest.raises(UnsafeEndpointError):
            await pick_safe_address("attacker.example.com")

    @pytest.mark.asyncio
    async def test_an_allowlisted_host_is_not_pinned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pinning these would break the local runtimes the allowlist exists for."""

        def _boom(_h: str) -> list[str]:  # pragma: no cover - must not run
            raise AssertionError("an allowlisted host must not be resolved")

        monkeypatch.setattr("app.core.net._resolve", _boom)
        assert await pick_safe_address("ollama") is None

    @pytest.mark.asyncio
    async def test_an_unresolvable_name_yields_no_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.core.net._resolve", lambda _h: [])
        assert await pick_safe_address("not-live-yet.example.com") is None


class _Captured(Exception):
    """Carries the request that reached the real transport."""

    def __init__(self, request: httpx.Request) -> None:
        self.request = request


async def _capture(self: Any, request: httpx.Request) -> httpx.Response:
    raise _Captured(request)


async def _send(
    monkeypatch: pytest.MonkeyPatch, url: str, resolved: Optional[list[str]]
) -> httpx.Request:
    """Return the request as the underlying transport would have received it."""
    if resolved is not None:
        monkeypatch.setattr("app.core.net._resolve", lambda _h: resolved)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _capture)

    transport = PinnedResolutionTransport()
    try:
        await transport.handle_async_request(httpx.Request("GET", url))
    except _Captured as captured:
        return captured.request
    raise AssertionError("the request never reached the transport")


class TestTransportPinning:
    @pytest.mark.asyncio
    async def test_it_connects_to_the_address_not_the_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request = await _send(
            monkeypatch, "https://api.example.com/v1/chat", ["93.184.216.34"]
        )
        assert request.url.host == "93.184.216.34"

    @pytest.mark.asyncio
    async def test_the_origin_still_sees_the_real_hostname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Virtual hosting breaks without this."""
        request = await _send(
            monkeypatch, "https://api.example.com/v1/chat", ["93.184.216.34"]
        )
        assert request.headers["Host"] == "api.example.com"

    @pytest.mark.asyncio
    async def test_tls_is_negotiated_against_the_hostname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without SNI the handshake would present the bare IP and every
        HTTPS provider would fail certificate verification."""
        request = await _send(
            monkeypatch, "https://api.example.com/v1/chat", ["93.184.216.34"]
        )
        assert request.extensions.get("sni_hostname") == "api.example.com"

    @pytest.mark.asyncio
    async def test_a_non_default_port_survives_in_the_host_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request = await _send(
            monkeypatch, "https://api.example.com:8443/v1", ["93.184.216.34"]
        )
        assert request.headers["Host"] == "api.example.com:8443"
        assert request.url.host == "93.184.216.34"

    @pytest.mark.asyncio
    async def test_a_rebound_name_never_reaches_the_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.core.net._resolve", lambda _h: ["127.0.0.1"])
        monkeypatch.setattr(
            httpx.AsyncHTTPTransport, "handle_async_request", _capture
        )
        transport = PinnedResolutionTransport()
        with pytest.raises(UnsafeEndpointError):
            await transport.handle_async_request(
                httpx.Request("GET", "http://attacker.example.com/v1")
            )

    @pytest.mark.asyncio
    async def test_an_ip_literal_passes_through_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """validate_api_base already settled it, and there is no name to resolve."""
        request = await _send(monkeypatch, "http://127.0.0.1:11434/v1", None)
        assert request.url.host == "127.0.0.1"
        assert "sni_hostname" not in request.extensions

    @pytest.mark.asyncio
    async def test_an_allowlisted_name_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request = await _send(monkeypatch, "http://ollama:11434/v1", None)
        assert request.url.host == "ollama"

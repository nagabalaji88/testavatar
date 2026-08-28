"""Cross-tenant read paths.

Both routes covered here authenticated the caller but never checked whether
that caller owned the thing being read, so each returned another user's
generated prompt content to any account on the instance.
"""

from __future__ import annotations

import inspect
from typing import Any, Optional

import pytest

from app.services.vector_service import VectorService


class _FakeClient:
    """Records the arguments the service hands to Qdrant."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        return []


def _service(monkeypatch: pytest.MonkeyPatch) -> tuple[VectorService, _FakeClient]:
    service = VectorService()
    fake = _FakeClient()

    async def ready() -> bool:
        return True

    async def client() -> _FakeClient:
        return fake

    async def embed(_text: str) -> list[float]:
        return [0.0, 1.0, 0.0]

    monkeypatch.setattr(service, "ensure_collection", ready)
    monkeypatch.setattr(service, "client", client)
    monkeypatch.setattr(service, "embed", embed)
    return service, fake


class TestSemanticSearchIsOwnerScoped:
    def test_owner_id_is_keyword_only_and_has_no_default(self) -> None:
        """A caller must state the scope; forgetting it cannot silently widen.

        The whole defect was a call site that passed only query/limit/score,
        so the parameter is deliberately not defaultable.
        """
        param = inspect.signature(VectorService.search).parameters["owner_id"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    @pytest.mark.asyncio
    async def test_a_scoped_search_filters_on_the_owner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, fake = _service(monkeypatch)
        await service.search("anything", owner_id="user-a")

        sent = fake.calls[0]["query_filter"]
        assert sent is not None, "a non-admin search must carry a filter"
        condition = sent.must[0]
        assert condition.key == "owner_id"
        assert condition.match.value == "user-a"

    @pytest.mark.asyncio
    async def test_an_admin_search_spans_every_owner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, fake = _service(monkeypatch)
        await service.search("anything", owner_id="admin", include_all_owners=True)
        assert fake.calls[0]["query_filter"] is None

    @pytest.mark.asyncio
    async def test_a_scoped_search_without_an_owner_returns_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail closed: no owner and no admin flag must not mean 'everything'."""
        service, fake = _service(monkeypatch)
        assert await service.search("anything", owner_id=None) == []
        assert not fake.calls, "no query may reach Qdrant unscoped"

    @pytest.mark.asyncio
    async def test_indexing_records_the_owner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without this on the point, the filter above can never match."""
        service = VectorService()
        captured: dict[str, Any] = {}

        class _Upserter:
            async def upsert(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        async def ready() -> bool:
            return True

        async def client() -> _Upserter:
            return _Upserter()

        async def embed(_text: str) -> list[float]:
            return [0.1, 0.2]

        monkeypatch.setattr(service, "ensure_collection", ready)
        monkeypatch.setattr(service, "client", client)
        monkeypatch.setattr(service, "embed", embed)

        await service.index_prompt(
            run_id="run-1",
            title="t",
            target_domain="d",
            content="c",
            score=90.0,
            owner_id="user-a",
        )
        assert captured["points"][0].payload["owner_id"] == "user-a"


class TestRunStreamIsAuthorized:
    def test_the_websocket_route_loads_and_authorizes_the_run(self) -> None:
        """Authenticating the token establishes who is calling, not what they may read.

        Asserted on the source because the check has no return value to
        observe -- the previous code called authenticate_websocket and threw
        the resulting principal away.
        """
        from app.api.v1 import endpoints

        source = inspect.getsource(endpoints.stream_run)
        assert "_load_run(" in source
        assert "_authorize_run(" in source
        assert source.index("_authorize_run(") < source.index("websocket.accept()"), (
            "authorization must happen before the handshake is accepted"
        )

    def test_every_run_read_path_authorizes(self) -> None:
        """The websocket was the one route that skipped the shared check."""
        from app.api.v1 import endpoints

        unguarded: list[str] = []
        for name in dir(endpoints):
            # The helper's own definition line matches the call pattern below.
            if name in {"_load_run", "_authorize_run"}:
                continue
            fn = getattr(endpoints, name)
            if not callable(fn) or not hasattr(fn, "__code__"):
                continue
            try:
                source = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            if "_load_run(" in source and "_authorize_run(" not in source:
                unguarded.append(name)
        assert not unguarded, f"these load a run without authorizing it: {unguarded}"


class TestStreamTickets:
    """A websocket handshake cannot carry a header, so its credential is in the URL.

    URLs reach proxy logs, access logs and browser history, which is why what
    goes there is a per-run, ~60s, single-use ticket rather than the account's
    access token.
    """

    @pytest.mark.asyncio
    async def test_a_ticket_opens_the_run_it_names(self) -> None:
        from app.core.security import Role, create_stream_ticket, redeem_stream_ticket

        user_id = "99999999-9999-9999-9999-999999999999"
        run_id = "11111111-1111-1111-1111-111111111111"
        ticket = create_stream_ticket(user_id, Role.ENGINEER, run_id)
        principal = await redeem_stream_ticket(ticket, run_id)
        assert str(principal.user_id) == user_id
        assert principal.role is Role.ENGINEER

    @pytest.mark.asyncio
    async def test_a_ticket_is_burned_on_first_use(self) -> None:
        from fastapi import HTTPException

        from app.core.security import Role, create_stream_ticket, redeem_stream_ticket

        run_id = "22222222-2222-2222-2222-222222222222"
        ticket = create_stream_ticket("99999999-9999-9999-9999-999999999999", Role.ENGINEER, run_id)
        await redeem_stream_ticket(ticket, run_id)
        with pytest.raises(HTTPException):
            await redeem_stream_ticket(ticket, run_id)

    @pytest.mark.asyncio
    async def test_a_ticket_does_not_open_a_different_run(self) -> None:
        from fastapi import HTTPException

        from app.core.security import Role, create_stream_ticket, redeem_stream_ticket

        ticket = create_stream_ticket(
            "99999999-9999-9999-9999-999999999999",
            Role.ENGINEER,
            "33333333-3333-3333-3333-333333333333",
        )
        with pytest.raises(HTTPException):
            await redeem_stream_ticket(
                ticket, "44444444-4444-4444-4444-444444444444"
            )

    @pytest.mark.asyncio
    async def test_an_access_token_is_not_a_ticket(self) -> None:
        """Otherwise the narrow credential could be swapped for the broad one."""
        from fastapi import HTTPException

        from app.core.security import Role, create_access_token, redeem_stream_ticket

        token = create_access_token("99999999-9999-9999-9999-999999999999", Role.ENGINEER)
        with pytest.raises(HTTPException):
            await redeem_stream_ticket(
                token, "55555555-5555-5555-5555-555555555555"
            )

    def test_a_ticket_expires_far_sooner_than_an_access_token(self) -> None:
        import jwt

        from app.core.config import settings
        from app.core.security import ALGORITHM, Role, create_stream_ticket

        claims = jwt.decode(
            create_stream_ticket("99999999-9999-9999-9999-999999999999", Role.ENGINEER, "r"),
            settings.jwt_secret_key,
            algorithms=[ALGORITHM],
        )
        lifetime = claims["exp"] - claims["iat"]
        assert lifetime <= settings.ws_ticket_ttl_seconds + 1
        assert lifetime < settings.access_token_ttl_minutes * 60


class TestTheLegacyTokenPathIsGone:
    """?token= accepted an account-wide, hour-long credential in a URL.

    It existed only so clients could migrate while the ticket flow shipped.
    The frontend has sent tickets since then, so the weaker credential is now
    refused rather than merely deprecated -- a path that still works is a path
    an old client, a stale bookmark or a copied URL keeps using.
    """

    def test_the_route_redeems_a_ticket_and_nothing_else(self) -> None:
        from app.api.v1 import endpoints

        source = inspect.getsource(endpoints.stream_run)
        assert "redeem_stream_ticket(" in source
        assert 'query_params.get("token")' not in source, (
            "the access-token path is back"
        )

    def test_the_helper_it_used_is_removed(self) -> None:
        """Left in place it invites a caller to reintroduce the same hole."""
        import app.core.security as security

        assert not hasattr(security, "authenticate_websocket")

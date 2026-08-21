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

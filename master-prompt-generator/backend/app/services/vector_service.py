"""Qdrant-backed semantic index over consensus prompts.

Indexing is best-effort: the vector store is an accelerator for search, never a
dependency of the generation pipeline, so every failure degrades to a no-op and
is logged rather than raised.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import litellm
from qdrant_client import AsyncQdrantClient, models as qmodels

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import SemanticSearchHit

logger = get_logger(__name__)


def _embedding_request() -> Optional[dict[str, Any]]:
    """Build the LiteLLM kwargs for the configured embedding backend.

    Returns None when embeddings are unavailable, which disables semantic
    search without affecting the generation pipeline.
    """
    provider = settings.embedding_provider
    if provider == "disabled":
        return None

    if provider == "ollama":
        # Fully local and key-free; the model must be pulled on the Ollama host.
        return {
            "model": settings.embedding_model,
            "api_base": settings.ollama_base_url,
        }

    if not settings.openai_api_key:
        return None
    return {"model": settings.embedding_model, "api_key": settings.openai_api_key}


class VectorService:
    def __init__(self) -> None:
        self._client: Optional[AsyncQdrantClient] = None
        self._ready = False

    async def client(self) -> Optional[AsyncQdrantClient]:
        if self._client is None:
            try:
                self._client = AsyncQdrantClient(
                    url=settings.qdrant_url,
                    api_key=settings.qdrant_api_key,
                    timeout=10,
                )
            except Exception as exc:
                logger.warning("qdrant_client_init_failed", extra={"error": str(exc)})
                return None
        return self._client

    async def ensure_collection(self) -> bool:
        if self._ready:
            return True
        client = await self.client()
        if client is None:
            return False
        try:
            collections = await client.get_collections()
            names = {collection.name for collection in collections.collections}
            if settings.qdrant_collection not in names:
                await client.create_collection(
                    collection_name=settings.qdrant_collection,
                    vectors_config=qmodels.VectorParams(
                        size=settings.embedding_dimensions,
                        distance=qmodels.Distance.COSINE,
                    ),
                )
                logger.info(
                    "qdrant_collection_created",
                    extra={"collection": settings.qdrant_collection},
                )
            self._ready = True
            return True
        except Exception as exc:
            logger.warning("qdrant_ensure_collection_failed", extra={"error": str(exc)})
            return False

    async def embed(self, text: str) -> Optional[list[float]]:
        request = _embedding_request()
        if request is None:
            return None
        try:
            response = await litellm.aembedding(input=[text[:8000]], **request)
            return list(response["data"][0]["embedding"])
        except Exception as exc:
            logger.warning("embedding_failed", extra={"error": str(exc)})
            return None

    async def index_prompt(
        self,
        *,
        run_id: str,
        title: str,
        target_domain: str,
        content: str,
        score: Optional[float],
        owner_id: Optional[str] = None,
    ) -> Optional[str]:
        if not await self.ensure_collection():
            return None
        vector = await self.embed(f"{title}\n{target_domain}\n{content}")
        if vector is None:
            return None

        client = await self.client()
        if client is None:
            return None

        point_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "run_id": run_id,
            "title": title,
            "target_domain": target_domain,
            "score": score,
            "excerpt": content[:600],
            # Carried so search can filter on it. A point written before this
            # field existed has no owner and is unreachable by any non-admin
            # search -- deliberately: the alternative is defaulting it to
            # something that matches, which would serve exactly the prompts
            # whose ownership is unknown.
            "owner_id": owner_id,
        }
        try:
            await client.upsert(
                collection_name=settings.qdrant_collection,
                points=[
                    qmodels.PointStruct(id=point_id, vector=vector, payload=payload)
                ],
            )
            return point_id
        except Exception as exc:
            logger.warning("qdrant_upsert_failed", extra={"error": str(exc)})
            return None

    async def search(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.0,
        *,
        owner_id: Optional[str],
        include_all_owners: bool = False,
    ) -> list[SemanticSearchHit]:
        """Search indexed prompts, restricted to one owner unless told otherwise.

        owner_id is keyword-only and has no default: the collection spans every
        tenant, so a caller that forgets to scope its search would otherwise
        return other people's prompt content. Admins pass
        include_all_owners=True to search the whole collection, mirroring the
        admin branch in list_runs.
        """
        if not await self.ensure_collection():
            return []
        vector = await self.embed(query)
        client = await self.client()
        if vector is None or client is None:
            return []

        query_filter = None
        if not include_all_owners:
            if not owner_id:
                return []
            query_filter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="owner_id",
                        match=qmodels.MatchValue(value=owner_id),
                    )
                ]
            )

        try:
            hits = await client.search(
                collection_name=settings.qdrant_collection,
                query_vector=vector,
                limit=limit,
                score_threshold=min_score or None,
                query_filter=query_filter,
            )
        except Exception as exc:
            logger.warning("qdrant_search_failed", extra={"error": str(exc)})
            return []

        results: list[SemanticSearchHit] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                SemanticSearchHit(
                    run_id=str(payload.get("run_id", "")),
                    title=str(payload.get("title", "Untitled")),
                    score=float(payload.get("score") or 0.0),
                    similarity=round(float(hit.score), 4),
                    excerpt=str(payload.get("excerpt", "")),
                    target_domain=str(payload.get("target_domain", "")),
                )
            )
        return results

    async def health(self) -> str:
        client = await self.client()
        if client is None:
            return "unavailable"
        try:
            await client.get_collections()
            return "ok"
        except Exception:
            return "unavailable"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._ready = False


vector_service = VectorService()

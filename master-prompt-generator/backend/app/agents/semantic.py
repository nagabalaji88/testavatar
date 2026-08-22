"""Meaning-aware similarity for the consensus merge.

The merge compares directives with a lexical score -- token overlap blended
with character-sequence ratio. That cannot see past wording, and the two
failures it produces are opposite and both wrong:

    "Never fabricate a policy number."
    "If the policy ID is absent, escalate rather than infer one."
        one rule, scored 0.18 -> shipped twice, each recorded as
        uncorroborated, so the strongest signal available (two models
        independently reached the same rule) is thrown away.

    "Return output as strict JSON."
    "Return output as strict YAML."
        two incompatible rules, scored 0.70 -> merged as duplicates and one
        silently dropped.

An embedding fixes the first directly: vectors of paraphrases sit close
together regardless of shared words. It does *not* fix the second -- JSON and
YAML are semantically adjacent, so a vector score makes that pair look more
alike, not less. Contradiction detection stays a separate concern, handled by
the polarity, numeric and mutually-exclusive-value checks in consensus.py.

Embeddings are computed once per run, in batch, and handed to the merge as a
lookup. The merge itself stays synchronous and pure: it is a function of its
inputs, one of which is now this index, so a test can supply a fake one and
still assert exact clustering.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SemanticIndex:
    """Precomputed vectors for the directives of a single run."""

    vectors: dict[str, list[float]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.vectors)

    def similarity(self, left: str, right: str) -> Optional[float]:
        """Cosine similarity, or None when either side was not embedded.

        None is a real answer, not an error: the caller falls back to the
        lexical score, which is what runs when embeddings are disabled or the
        provider failed. Returning 0.0 instead would assert the two directives
        are unrelated, which is exactly the wrong claim to make on missing data.
        """
        a = self.vectors.get(left.strip())
        b = self.vectors.get(right.strip())
        if a is None or b is None:
            return None
        return _cosine(a, b)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    # Clamped because floating-point error can push an identical pair a hair
    # above 1.0, and callers compare against thresholds expressed in [0, 1].
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


async def build_semantic_index(texts: Sequence[str]) -> SemanticIndex:
    """Embed `texts` once and return the lookup the merge consults.

    Never raises and never partially succeeds. An empty index means the merge
    runs exactly as it did before this existed, so a disabled or broken
    embedding provider degrades the merge's precision rather than the run.
    """
    from app.services.vector_service import vector_service

    unique = sorted({text.strip() for text in texts if text and text.strip()})
    if not unique:
        return SemanticIndex()

    vectors = await vector_service.embed_many(unique)
    if vectors is None or len(vectors) != len(unique):
        logger.info(
            "semantic_index_unavailable_using_lexical_merge",
            extra={"directives": len(unique)},
        )
        return SemanticIndex()

    logger.info("semantic_index_built", extra={"directives": len(unique)})
    return SemanticIndex(vectors=dict(zip(unique, vectors)))

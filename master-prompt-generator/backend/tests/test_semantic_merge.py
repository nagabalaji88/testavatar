"""Meaning-aware directive comparison.

The lexical measure blends token overlap with a character-sequence ratio, so it
cannot see past wording. Two models writing one rule in different words score
~0.18 -- below the clustering threshold -- so the merge ships both and records
each as uncorroborated, discarding the agreement a fan-out exists to find.

Vectors here are hand-built rather than fetched: the merge must stay a pure
function of its inputs, and that is only demonstrable if a test can supply the
index itself.
"""

from __future__ import annotations

import pytest

from app.agents.consensus import (
    SIMILARITY_SUBJECT,
    _contradiction,
    cluster_directives,
    lexical_similarity,
    similarity,
)
from app.agents.semantic import SemanticIndex, _cosine, build_semantic_index

PARAPHRASE_A = "Never fabricate a policy number."
PARAPHRASE_B = "If the policy ID is absent, escalate rather than infer one."
UNRELATED = "Respond only in formal British English."


def _index() -> SemanticIndex:
    """A and B near-parallel (one rule, two phrasings); C orthogonal."""
    return SemanticIndex(
        vectors={
            PARAPHRASE_A: [1.0, 0.0, 0.0],
            PARAPHRASE_B: [0.92, 0.39, 0.0],
            UNRELATED: [0.0, 0.0, 1.0],
        }
    )


class TestSimilarityWithMeaning:
    def test_the_lexical_measure_misses_a_paraphrase(self) -> None:
        """The defect this exists to fix, pinned so it cannot be argued away."""
        assert lexical_similarity(PARAPHRASE_A, PARAPHRASE_B) < SIMILARITY_SUBJECT

    def test_the_index_rescues_it(self) -> None:
        assert similarity(PARAPHRASE_A, PARAPHRASE_B, _index()) >= SIMILARITY_SUBJECT

    def test_genuinely_unrelated_directives_stay_apart(self) -> None:
        """The fix must not merge everything into one cluster."""
        assert similarity(PARAPHRASE_A, UNRELATED, _index()) < SIMILARITY_SUBJECT

    def test_semantics_can_only_raise_a_score_never_lower_it(self) -> None:
        """A near-identical pair is already strong evidence on its own.

        The two measures are blended with max() so a weak vector score can
        never pull apart a pair the lexical measure matched.
        """
        text = "Always cite the source document."
        other = "Always cite the source document, verbatim."
        misleading = SemanticIndex(vectors={text: [1.0, 0.0], other: [0.0, 1.0]})
        assert similarity(text, other, misleading) == lexical_similarity(text, other)

    def test_an_absent_directive_falls_back_rather_than_scoring_zero(self) -> None:
        """Missing data must not be reported as 'unrelated'."""
        partial = SemanticIndex(vectors={PARAPHRASE_A: [1.0, 0.0, 0.0]})
        assert partial.similarity(PARAPHRASE_A, PARAPHRASE_B) is None
        assert similarity(PARAPHRASE_A, PARAPHRASE_B, partial) == lexical_similarity(
            PARAPHRASE_A, PARAPHRASE_B
        )

    def test_no_index_is_exactly_the_previous_behaviour(self) -> None:
        for left, right in [
            (PARAPHRASE_A, PARAPHRASE_B),
            (PARAPHRASE_A, UNRELATED),
            ("Return strict JSON.", "Return strict JSON."),
        ]:
            assert similarity(left, right) == lexical_similarity(left, right)


class TestClusteringUsesIt:
    def _variant(self, model_id: str, units: list[str]):
        from app.agents.consensus import SectionVariant

        return SectionVariant(
            canonical="instructions",
            title="Instructions",
            body="\n".join(units),
            units=units,
            model_id=model_id,
            model_name=model_id,
            score=80.0,
        )

    def test_two_models_phrasing_one_rule_differently_now_corroborate(self) -> None:
        """This is the whole point: support is the fan-out's strongest signal."""
        variants = [
            self._variant("model-a", [PARAPHRASE_A]),
            self._variant("model-b", [PARAPHRASE_B]),
        ]

        lexical_clusters = cluster_directives(variants, set())
        assert len(lexical_clusters) == 2
        assert all(c.support == 1 for c in lexical_clusters), (
            "the lexical merge sees two unrelated rules"
        )

        semantic_clusters = cluster_directives(variants, set(), _index())
        assert len(semantic_clusters) == 1
        assert semantic_clusters[0].support == 2
        assert semantic_clusters[0].contributing_models == ["model-a", "model-b"]

    def test_distinct_rules_are_not_collapsed(self) -> None:
        variants = [
            self._variant("model-a", [PARAPHRASE_A]),
            self._variant("model-b", [UNRELATED]),
        ]
        assert len(cluster_directives(variants, set(), _index())) == 2

    def test_the_merge_stays_deterministic(self) -> None:
        """Same inputs and same index must give the same clusters, every time."""
        variants = [
            self._variant("model-a", [PARAPHRASE_A]),
            self._variant("model-b", [PARAPHRASE_B, UNRELATED]),
        ]
        runs = [
            [(c.representative.text, c.support) for c in cluster_directives(
                variants, set(), _index()
            )]
            for _ in range(5)
        ]
        assert all(run == runs[0] for run in runs)


class TestContradictionsSemanticsWouldHide:
    def test_two_output_formats_are_a_conflict_not_a_duplicate(self) -> None:
        """Embeddings make this pair look *more* alike, so it is caught by name.

        JSON and YAML are semantically adjacent, so a vector score pushes them
        together -- toward being merged as restatements, silently dropping one
        side of a real disagreement about the output contract.
        """
        verdict = _contradiction(
            "Return output as strict JSON.", "Return output as strict YAML."
        )
        assert verdict is not None
        assert "output formats" in verdict[0]

    def test_the_same_format_twice_is_still_a_duplicate(self) -> None:
        assert (
            _contradiction(
                "Return output as strict JSON.",
                "Return output as strict JSON with a schema.",
            )
            is None
        )

    def test_the_existing_numeric_and_polarity_checks_still_fire(self) -> None:
        assert _contradiction("Escalate below 0.7.", "Escalate below 0.9.")
        assert _contradiction("Always cite sources.", "Never cite sources.")


class TestIndexConstruction:
    def test_cosine_is_clamped_and_orientation_aware(self) -> None:
        assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
        # Mismatched widths mean the vectors came from different models; that
        # is a configuration error, not a similarity of any value.
        assert _cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0
        assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0

    @pytest.mark.asyncio
    async def test_a_failed_embedding_call_yields_an_empty_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The merge must degrade in precision, never fail the run."""
        from app.services import vector_service as module

        async def _fail(_texts):
            return None

        monkeypatch.setattr(module.vector_service, "embed_many", _fail)
        index = await build_semantic_index([PARAPHRASE_A, PARAPHRASE_B])
        assert not index
        assert similarity(PARAPHRASE_A, PARAPHRASE_B, index) == lexical_similarity(
            PARAPHRASE_A, PARAPHRASE_B
        )

    @pytest.mark.asyncio
    async def test_directives_are_embedded_once_each(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repeats across candidates are the norm; paying per copy is waste."""
        from app.services import vector_service as module

        seen: list[list[str]] = []

        async def _capture(texts):
            seen.append(list(texts))
            return [[1.0, 0.0] for _ in texts]

        monkeypatch.setattr(module.vector_service, "embed_many", _capture)
        await build_semantic_index([PARAPHRASE_A, PARAPHRASE_A, PARAPHRASE_B, "  "])

        assert len(seen) == 1, "one batched call, not one per directive"
        assert sorted(seen[0]) == sorted({PARAPHRASE_A, PARAPHRASE_B})

    @pytest.mark.asyncio
    async def test_no_directives_means_no_call_at_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import vector_service as module

        async def _boom(_texts):  # pragma: no cover - must not run
            raise AssertionError("embedding must not be called for an empty set")

        monkeypatch.setattr(module.vector_service, "embed_many", _boom)
        assert not await build_semantic_index([])

"""Unit tests for the deterministic phases of the Consensus Synthesis Engine."""

from __future__ import annotations

import pytest

from app.agents.consensus import (
    CandidateInput,
    canonicalize_heading,
    consensus_engine,
    detect_conflicts,
    extract_variants,
    optimize,
    parse_sections,
    resolve_conflicts,
    similarity,
    split_units,
)
from app.models.schemas import METRIC_KEYS, JudgeVerdict

PROMPT_A = """## Role & Objective
You are a senior claims triage assistant for a commercial insurer.

## Instructions
- Classify every inbound notice into one of the four severity tiers.
- Extract the policy number, incident date and estimated exposure.
- Do not quote a settlement figure to the claimant.

## Output Format
Respond with a JSON object matching the schema { "tier": string, "policy": string }.

## Constraints & Guardrails
- Never invent a policy number that is absent from the source document.
- Escalate to a human adjuster when confidence is below 0.7.
"""

PROMPT_B = """## Persona
You are a senior claims triage assistant for a commercial insurer.

## Task Directives
- Classify every inbound notice into one of the four severity tiers.
- Record the reporting channel and the claimant's preferred contact time.
- Escalate to a human adjuster when confidence is below 0.9.

## Response Format
Return YAML with the keys tier, policy and exposure.

## Failure Handling
- If the document cannot be parsed, return tier "unknown" and flag for manual review.
"""


def _verdict(prompt_id: str, score: float) -> JudgeVerdict:
    return JudgeVerdict(
        prompt_id=prompt_id,
        overall_score=score,
        metrics={key: score for key in METRIC_KEYS},
    )


@pytest.fixture()
def candidates() -> list[CandidateInput]:
    return [
        CandidateInput("model-a", "Model A", PROMPT_A, _verdict("model-a", 88.0)),
        CandidateInput("model-b", "Model B", PROMPT_B, _verdict("model-b", 74.0)),
    ]


class TestSectionParsing:
    def test_headings_map_onto_the_canonical_taxonomy(self) -> None:
        assert canonicalize_heading("Persona") == "role"
        assert canonicalize_heading("Task Directives") == "instructions"
        assert canonicalize_heading("Response Format") == "output"
        assert canonicalize_heading("Failure Handling") == "failure"
        assert canonicalize_heading("Something Unrelated") == "additional"

    def test_parse_sections_preserves_bodies(self) -> None:
        sections = parse_sections(PROMPT_A)
        keys = [key for key, _, _ in sections]
        assert keys == ["role", "instructions", "output", "constraints"]
        assert "four severity tiers" in dict((k, b) for k, _, b in sections)["instructions"]

    def test_split_units_separates_bullets(self) -> None:
        units = split_units("- one directive\n- another directive\n\nA closing sentence.")
        assert len(units) == 3
        assert units[0].startswith("- one")

    def test_preamble_without_headings_is_treated_as_the_role_section(self) -> None:
        sections = parse_sections("You are a helpful assistant.\n\n## Output Format\nJSON.")
        assert sections[0][0] == "role"
        assert sections[0][2] == "You are a helpful assistant."


class TestSimilarity:
    def test_identical_text_scores_one(self) -> None:
        assert similarity("do not fabricate data", "do not fabricate data") == 1.0

    def test_unrelated_text_scores_low(self) -> None:
        assert similarity("emit strict json", "escalate to a human adjuster") < 0.35


class TestConflictDetection:
    def test_numeric_disagreement_is_detected_across_sections(
        self, candidates: list[CandidateInput]
    ) -> None:
        """The two models file the same escalation rule under different headings."""
        index = extract_variants(candidates)
        by_section, losers = resolve_conflicts(index)
        semantic = [
            conflict
            for conflicts in by_section.values()
            for conflict in conflicts
            if conflict.kind == "semantic"
        ]
        assert semantic, "conflicting confidence thresholds should be detected"
        assert all(conflict.winner_model_id == "model-a" for conflict in semantic)
        assert any("0.9" in unit for unit in losers), "the weaker threshold must lose"

    def test_competing_output_formats_are_reported_as_syntactic(
        self, candidates: list[CandidateInput]
    ) -> None:
        index = extract_variants(candidates)
        outcome = detect_conflicts("output", index["output"])
        kinds = {conflict.kind for conflict in outcome.records}
        assert "syntactic" in kinds

    def test_divergent_headings_are_reported_as_structural(
        self, candidates: list[CandidateInput]
    ) -> None:
        index = extract_variants(candidates)
        outcome = detect_conflicts("role", index["role"])
        assert any(conflict.kind == "structural" for conflict in outcome.records)

    def test_agreeing_models_produce_no_semantic_conflict(self) -> None:
        shared = "## Instructions\n- Cite the clause number for every obligation.\n"
        index = extract_variants(
            [
                CandidateInput("a", "A", shared, _verdict("a", 80.0)),
                CandidateInput("b", "B", shared, _verdict("b", 78.0)),
            ]
        )
        by_section, losers = resolve_conflicts(index)
        assert not [
            conflict
            for conflicts in by_section.values()
            for conflict in conflicts
            if conflict.kind == "semantic"
        ]
        assert not losers


class TestMerge:
    def test_merge_keeps_unique_directives_from_both_models(
        self, candidates: list[CandidateInput]
    ) -> None:
        merged, provenance, conflicts = consensus_engine.synthesize_deterministic(
            candidates
        )
        # Section only the weaker model supplied still survives.
        assert "## Failure Handling" in merged
        # A directive unique to the weaker model is grafted in.
        assert "preferred contact time" in merged
        # The stronger model wins the sections both supplied.
        role = next(item for item in provenance if item.section == "Role & Objective")
        assert role.source_model_id == "model-a"
        assert conflicts

    def test_only_one_output_contract_survives(
        self, candidates: list[CandidateInput]
    ) -> None:
        """A merged prompt must never carry two competing output formats."""
        merged, _, _ = consensus_engine.synthesize_deterministic(candidates)
        output_section = merged.split("## Output Format")[1].split("##")[0].lower()
        assert "json" in output_section
        assert "yaml" not in output_section

    def test_losing_side_of_a_numeric_conflict_is_removed(
        self, candidates: list[CandidateInput]
    ) -> None:
        merged, _, _ = consensus_engine.synthesize_deterministic(candidates)
        assert "below 0.7" in merged
        assert "below 0.9" not in merged

    def test_duplicate_directives_are_not_repeated(
        self, candidates: list[CandidateInput]
    ) -> None:
        merged, _, _ = consensus_engine.synthesize_deterministic(candidates)
        assert merged.lower().count("four severity tiers") == 1

    def test_single_candidate_round_trips(self) -> None:
        merged, provenance, conflicts = consensus_engine.synthesize_deterministic(
            [CandidateInput("solo", "Solo", PROMPT_A, _verdict("solo", 91.0))]
        )
        assert "## Role & Objective" in merged
        assert not conflicts
        assert all(item.strategy == "adopted" for item in provenance)

    def test_empty_input_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            consensus_engine.synthesize_deterministic([])


class TestOptimizer:
    def test_duplicate_lines_and_blank_runs_are_collapsed(self) -> None:
        noisy = (
            "# Role\n\n\n\n"
            "Always ground every claim in the provided source document.\n"
            "Always ground every claim in the provided source document.\n"
        )
        optimized, report = optimize(noisy)
        assert report.removed_duplicate_lines == 1
        assert report.collapsed_whitespace_blocks >= 1
        assert report.normalized_headings == 1
        assert optimized.startswith("## Role")
        assert report.optimized_tokens <= report.original_tokens

    def test_code_fences_are_preserved_verbatim(self) -> None:
        source = '## Output Format\n\n```json\n{\n  "tier": "string"\n}\n```\n'
        optimized, _ = optimize(source)
        assert '"tier": "string"' in optimized
        assert optimized.count("```") == 2

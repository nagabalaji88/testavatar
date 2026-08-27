"""Unit tests for the Multi-Model Debate Engine.

Every provider call goes through a scripted fake LLMService, so the whole
three-round protocol runs offline and deterministically.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from app.agents.debate import (
    CROSS,
    OPENING,
    SYNTHESIS,
    DebateEngine,
    DebateError,
    extract_revised_answer,
    label_for,
    pick_judge,
    rotate,
)
from app.models.schemas import ProviderConfig
from app.services.llm_service import LLMError, LLMFailure, LLMResult


def make_provider(
    provider_id: str, *, weight: float = 1.0, name: str | None = None
) -> ProviderConfig:
    return ProviderConfig(
        id=provider_id,
        name=name or provider_id.upper(),
        provider="test",
        model_key=f"test/{provider_id}",
        max_tokens=1024,
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.002,
        weight=weight,
    )


class FakeLLMService:
    """Scripted stand-in for LLMService.

    ``script`` maps (model_id, phase) to either a string response or an
    exception to raise. Every prompt pair is recorded so tests can assert on
    what each model was actually shown.
    """

    def __init__(self, script: dict[tuple[str, str], str | Exception]) -> None:
        self.script = script
        self.prompts: list[tuple[str, str, str, str]] = []

    def _respond(
        self, provider: ProviderConfig, phase: str, system: str, user: str
    ) -> LLMResult:
        self.prompts.append((provider.id, phase, system, user))
        outcome = self.script.get((provider.id, phase), f"{provider.id}:{phase}")
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResult(
            model_id=provider.id,
            model_name=provider.name,
            provider=provider.provider,
            content=outcome,
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.001,
            latency_ms=5,
            attempts=1,
        )

    async def complete(
        self,
        provider: ProviderConfig,
        *,
        system_prompt: str,
        user_prompt: str,
        phase: str,
        **_kwargs: object,
    ) -> LLMResult:
        return self._respond(provider, phase, system_prompt, user_prompt)

    async def fan_out(
        self,
        providers: Sequence[ProviderConfig],
        *,
        build_messages: Callable[[ProviderConfig], tuple[str, str]],
        phase: str,
        **_kwargs: object,
    ) -> tuple[list[LLMResult], list[LLMFailure]]:
        results: list[LLMResult] = []
        failures: list[LLMFailure] = []
        for provider in providers:
            system, user = build_messages(provider)
            try:
                results.append(self._respond(provider, phase, system, user))
            except Exception as exc:
                failures.append(
                    LLMFailure(
                        model_id=provider.id,
                        model_name=provider.name,
                        provider=provider.provider,
                        error=str(exc),
                        attempts=1,
                        latency_ms=5,
                    )
                )
        return results, failures

    def prompts_for(self, phase: str) -> dict[str, str]:
        """User prompts keyed by model id for one phase."""
        return {p[0]: p[3] for p in self.prompts if p[1] == phase}


def engine_with(
    script: dict[tuple[str, str], str | Exception],
) -> tuple[DebateEngine, FakeLLMService]:
    fake = FakeLLMService(script)
    return DebateEngine(fake), fake  # type: ignore[arg-type]


THREE = [make_provider("alpha"), make_provider("beta"), make_provider("gamma")]


# --- happy path ------------------------------------------------------------


async def test_three_models_produce_three_rounds_and_a_judged_answer() -> None:
    engine, _ = engine_with({("gamma", f"debate_{SYNTHESIS}"): "The judged answer."})
    providers = [
        make_provider("alpha"),
        make_provider("beta"),
        make_provider("gamma", weight=2.0),
    ]

    result = await engine.debate("Which database should we use?", providers)

    assert [r.stage for r in result.rounds] == [OPENING, CROSS, SYNTHESIS]
    assert len(result.rounds[0].contributions) == 3
    assert len(result.rounds[1].contributions) == 3
    assert result.final_answer == "The judged answer."
    assert result.judge_model_id == "gamma"  # heaviest provider judges
    assert not result.judge_fell_back
    assert not result.solo_mode
    assert result.participants == ["ALPHA", "BETA", "GAMMA"]


async def test_usage_is_accumulated_across_every_round() -> None:
    engine, _ = engine_with({})
    result = await engine.debate("q", THREE)

    # 3 openings + 3 critiques + 1 synthesis = 7 calls at 10/20 tokens each.
    assert result.input_tokens == 70
    assert result.output_tokens == 140
    assert result.cost_usd == pytest.approx(0.007)


# --- anonymity and rotation ------------------------------------------------


async def test_openings_are_anonymised_before_cross_examination() -> None:
    engine, fake = engine_with({})
    await engine.debate("q", THREE)

    critique_prompt = fake.prompts_for(f"debate_{CROSS}")["alpha"]
    assert "Response A" in critique_prompt
    # Vendor identity must not leak, or critics stop judging on the merits.
    for provider in THREE:
        assert provider.name not in critique_prompt


async def test_each_critic_leads_with_a_different_opening() -> None:
    engine, fake = engine_with({})
    await engine.debate("q", THREE)

    prompts = fake.prompts_for(f"debate_{CROSS}")
    first_seen = {
        model_id: prompt.split("### ")[1].split("\n")[0]
        for model_id, prompt in prompts.items()
    }
    assert len(set(first_seen.values())) == 3


async def test_transcript_keeps_registry_order_not_completion_order() -> None:
    engine, _ = engine_with({})
    result = await engine.debate("q", THREE)

    labels = [c.label for c in result.rounds[0].contributions]
    assert labels == ["Response A", "Response B", "Response C"]


# --- failure handling ------------------------------------------------------


async def test_a_model_failing_the_opening_round_is_dropped() -> None:
    engine, _ = engine_with(
        {("beta", f"debate_{OPENING}"): LLMError("429", model_id="beta", retryable=True)}
    )
    result = await engine.debate("q", THREE)

    assert len(result.rounds[0].contributions) == 2
    assert [f.model_id for f in result.rounds[0].failures] == ["beta"]
    # A model with no opening does not get to cross-examine.
    assert len(result.rounds[1].contributions) == 2


async def test_every_model_failing_the_opening_round_is_fatal() -> None:
    engine, _ = engine_with(
        {
            (p.id, f"debate_{OPENING}"): LLMError("boom", model_id=p.id, retryable=True)
            for p in THREE
        }
    )
    with pytest.raises(DebateError, match="opening round"):
        await engine.debate("q", THREE)


async def test_judge_failure_falls_back_to_a_revised_answer() -> None:
    critique = "## Assessment\nfine\n\n## My Revised Answer\nThe fallback answer.\n"
    engine, _ = engine_with(
        {
            ("gamma", f"debate_{CROSS}"): critique,
            ("gamma", f"debate_{SYNTHESIS}"): LLMError(
                "overloaded", model_id="gamma", retryable=True
            ),
        }
    )
    providers = [
        make_provider("alpha"),
        make_provider("beta"),
        make_provider("gamma", weight=2.0),
    ]

    result = await engine.debate("q", providers)

    assert result.judge_fell_back
    assert result.final_answer == "The fallback answer."
    assert result.rounds[2].failures[0].model_id == "gamma"


async def test_debate_still_concludes_when_the_whole_cross_round_fails() -> None:
    engine, _ = engine_with(
        {
            (p.id, f"debate_{CROSS}"): LLMError("boom", model_id=p.id, retryable=True)
            for p in THREE
        }
        | {("alpha", f"debate_{SYNTHESIS}"): "Judged from openings alone."}
    )
    result = await engine.debate("q", THREE)

    assert result.rounds[1].contributions == []
    assert result.final_answer == "Judged from openings alone."
    assert not result.judge_fell_back


# --- solo mode -------------------------------------------------------------


async def test_a_single_model_runs_self_critique_instead_of_cross_examination() -> None:
    engine, fake = engine_with({("alpha", f"debate_{SYNTHESIS}"): "Solo answer."})
    result = await engine.debate("q", [make_provider("alpha")])

    assert result.solo_mode
    assert result.final_answer == "Solo answer."
    solo_system = next(
        p[2] for p in fake.prompts if p[1] == f"debate_{CROSS}" and p[0] == "alpha"
    )
    assert "hostile critic" in solo_system


# --- validation ------------------------------------------------------------


async def test_a_blank_question_is_rejected() -> None:
    engine, _ = engine_with({})
    with pytest.raises(DebateError, match="needs a question"):
        await engine.debate("   ", THREE)


async def test_no_providers_is_rejected() -> None:
    engine, _ = engine_with({})
    with pytest.raises(DebateError, match="No models"):
        await engine.debate("q", [])


# --- pure helpers ----------------------------------------------------------


def test_label_for_walks_the_alphabet_then_falls_back_to_numbers() -> None:
    assert label_for(0) == "Response A"
    assert label_for(25) == "Response Z"
    assert label_for(26) == "Response 27"


def test_rotate_is_a_left_rotation_and_tolerates_overflow() -> None:
    items = ["a", "b", "c"]
    assert rotate(items, 1) == ["b", "c", "a"]  # type: ignore[arg-type]
    assert rotate(items, 4) == ["b", "c", "a"]  # type: ignore[arg-type]
    assert rotate([], 2) == []


def test_extract_revised_answer_reads_the_section() -> None:
    text = (
        "## Assessment\nStrong.\n\n"
        "## Disagreements\nNone substantive\n\n"
        "## My Revised Answer\nUse Postgres.\n\nIt is the boring choice.\n"
    )
    assert extract_revised_answer(text) == "Use Postgres.\n\nIt is the boring choice."


def test_extract_revised_answer_stops_at_the_next_heading() -> None:
    text = "## My Revised Answer\nThe answer.\n\n## Notes\nignore me"
    assert extract_revised_answer(text) == "The answer."


def test_extract_revised_answer_returns_none_when_the_format_was_ignored() -> None:
    assert extract_revised_answer("Just prose, no headings at all.") is None


def test_pick_judge_prefers_the_heaviest_survivor() -> None:
    providers = [
        make_provider("alpha", weight=1.0),
        make_provider("beta", weight=3.0),
        make_provider("gamma", weight=2.0),
    ]
    assert pick_judge(providers, {"alpha", "beta", "gamma"}).id == "beta"  # type: ignore[union-attr]
    # The heaviest model is out, so the next heaviest judges.
    assert pick_judge(providers, {"alpha", "gamma"}).id == "gamma"  # type: ignore[union-attr]
    assert pick_judge(providers, set()) is None

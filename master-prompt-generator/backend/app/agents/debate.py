"""Multi-Model Debate Engine.

Turns one question into one answer by making the models argue about it:

  1. OPENING    — every provider answers independently, in parallel, with no
                  knowledge of the others.
  2. CROSS      — every provider sees all the openings, anonymised, and must
                  assess them, settle the disagreements and revise its own
                  answer.
  3. SYNTHESIS  — one judge reads the whole transcript and writes the answer
                  the user actually receives.

This is a different mechanism from the Consensus Synthesis Engine, which merges
scored *prompt candidates* section by section. Consensus never lets a model read
another model's work; debate is entirely about that. Consensus produces a
composite artefact, debate produces a decision.

Two details do most of the work in round 2:

  * **Anonymity.** Openings are relabelled "Response A/B/C" and each critic is
    told one of them is its own without being told which. Models rate text they
    recognise as their own more highly, which collapses the round into mutual
    agreement.
  * **Rotation.** Each critic receives the openings in a different order,
    because models over-weight whatever they read first.

Failure policy: a provider that fails is dropped from that round and the debate
continues without it. Only two situations are fatal — no providers, and every
provider failing the opening round. A judge that fails falls back to the best
revised answer already produced, so a late failure never discards two rounds of
paid work.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.models.schemas import (
    DebateContribution,
    DebateFailure,
    DebateRead,
    DebateRoundRead,
    ProviderConfig,
)
from app.services.llm_service import LLMResult, LLMService, llm_service

logger = get_logger(__name__)

# --- Rounds ----------------------------------------------------------------

OPENING = "opening"
CROSS = "cross_examination"
SYNTHESIS = "synthesis"

STAGE_TITLES: dict[str, str] = {
    OPENING: "Opening statements",
    CROSS: "Cross-examination",
    SYNTHESIS: "Synthesis",
}

# Heading the critique round is asked to produce, and which the judge-failure
# fallback harvests.
REVISED_HEADING = re.compile(
    r"^#{1,6}\s*My\s+Revised\s+Answer\s*$", re.IGNORECASE | re.MULTILINE
)
ANY_HEADING = re.compile(r"^#{1,6}\s+\S")


# --- Result structures -----------------------------------------------------


@dataclass(slots=True)
class Contribution:
    """One model's output in one round."""

    model_id: str
    model_name: str
    provider: str
    label: str
    content: str
    latency_ms: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True)
class RoundFailure:
    model_id: str
    model_name: str
    error: str


@dataclass(slots=True)
class DebateRound:
    stage: str
    contributions: list[Contribution] = field(default_factory=list)
    failures: list[RoundFailure] = field(default_factory=list)

    @property
    def title(self) -> str:
        return STAGE_TITLES.get(self.stage, self.stage)


@dataclass(slots=True)
class DebateResult:
    question: str
    rounds: list[DebateRound]
    final_answer: str
    judge_model_id: str
    judge_model_name: str
    judge_fell_back: bool
    solo_mode: bool
    elapsed_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float

    @property
    def participants(self) -> list[str]:
        opening = next((r for r in self.rounds if r.stage == OPENING), None)
        if opening is None:
            return []
        return [c.model_name for c in opening.contributions]


class DebateError(RuntimeError):
    """Raised when a debate cannot start or every model failed round one."""


# --- Prompts ---------------------------------------------------------------

_ROLE = (
    "You are taking part in a structured debate between several AI models, each "
    "from a different vendor. A judge model reads the whole debate at the end and "
    "writes the answer the user actually sees."
)


def opening_system() -> str:
    return (
        f"{_ROLE}\n\n"
        "This is the opening round. You are answering on your own — you cannot see "
        "the other participants yet.\n\n"
        "Answer the user's question directly and well. Be specific and concrete. "
        "State any assumption you had to make. If the question has a factual "
        "answer, give it plainly. If it turns on judgement or tradeoffs, commit to "
        "a recommendation and show the reasoning that drives it — a survey of "
        "options with no verdict is a weak opening and will score badly."
    )


def cross_system() -> str:
    return (
        f"{_ROLE}\n\n"
        "This is the cross-examination round. You will see every opening answer, "
        "anonymised. One of them is yours; you are not told which, and you should "
        "not try to work it out. Judge them all on the merits.\n\n"
        "Respond under these exact headings:\n\n"
        "## Assessment\n"
        "For each response, give its strongest point and its weakest point. Quote "
        "or paraphrase the specific claim you mean — vague praise is not useful. If "
        "a response states something false, say so plainly and say what is actually "
        "true.\n\n"
        "## Disagreements\n"
        "Name the substantive disagreements between the responses. For each one, say "
        "which side is right and why. If they genuinely agree throughout, write "
        '"None substantive" and move on — do not manufacture a disagreement.\n\n'
        "## My Revised Answer\n"
        "The best answer you can now give to the original question, informed by "
        "everything above. Write it as a complete standalone answer for someone who "
        "has not read the debate. Do not describe how you changed your mind."
    )


def solo_cross_system() -> str:
    """Round 2 framing when only one provider is available."""
    return (
        "You are reviewing a draft answer as a hostile critic, then rewriting it.\n\n"
        "Only one model answered, so there is no one to disagree with. Attack the "
        "draft as an expert reviewer would, then produce something better.\n\n"
        "Respond under these exact headings:\n\n"
        "## Assessment\n"
        "The draft's strongest point, and its weakest. Then the errors, unstated "
        "assumptions and omissions you can find. Be genuinely critical — a review "
        "that finds nothing wrong is a failed review.\n\n"
        "## Disagreements\n"
        'Write "None substantive" — there is only one response in this debate.\n\n'
        "## My Revised Answer\n"
        "A complete standalone answer to the original question that fixes everything "
        "you just identified."
    )


def synthesis_system(*, solo_mode: bool) -> str:
    middle = (
        "Only one model took part, so you are weighing a draft against its own "
        "critical review rather than adjudicating between rivals.\n\n"
        if solo_mode
        else "You will see the original question, the anonymised opening answers, and "
        "each participant's cross-examination and revised answer.\n\n"
    )
    return (
        "You are the judge of a structured debate between AI models. You did not "
        "compete. Your job is to produce the single best answer for the user, who "
        "has not seen the debate and does not care who said what.\n\n"
        f"{middle}"
        "Weigh the arguments on the merits, not by how confident they sound or how "
        "many participants said the same thing — a point made well by one is worth "
        "more than an error repeated by three. Where they disagree, decide. Where "
        "they all missed something important, add it yourself.\n\n"
        "Respond under these exact headings:\n\n"
        "## Best Answer\n"
        "The answer to the original question. Self-contained, directly useful, "
        "written for the user. This is what they came for, so give it the most space "
        "and do not hedge it with debate commentary.\n\n"
        "## Why This Answer\n"
        "Two to four sentences: what the debate settled, and what tipped it.\n\n"
        "## Scorecard\n"
        "One line per response — its label, a score out of 10, and a short reason."
    )


def _render_contributions(contributions: Sequence[Contribution], suffix: str = "") -> str:
    blocks: list[str] = []
    for contribution in contributions:
        heading = f"### {contribution.label}{suffix}"
        blocks.append(f"{heading}\n{contribution.content}")
    return "\n\n".join(blocks)


def cross_user(question: str, openings: Sequence[Contribution]) -> str:
    return (
        f"The user's question:\n\n{question}\n\n---\n\n"
        f"The opening answers:\n\n{_render_contributions(openings)}"
    )


def solo_cross_user(question: str, draft: Contribution) -> str:
    return (
        f"The user's question:\n\n{question}\n\n---\n\n"
        f"The draft answer:\n\n{draft.content}"
    )


def synthesis_user(
    question: str,
    openings: Sequence[Contribution],
    critiques: Sequence[Contribution],
) -> str:
    parts = [
        f"The user's question:\n\n{question}",
        f"---\n\nOpening answers:\n\n{_render_contributions(openings)}",
    ]
    if critiques:
        parts.append(
            "---\n\nCross-examination and revised answers:\n\n"
            f"{_render_contributions(critiques, suffix=' (cross-examination)')}"
        )
    return "\n\n".join(parts)


# --- Helpers ---------------------------------------------------------------


def label_for(index: int) -> str:
    """Anonymous handle shown to the other models."""
    if 0 <= index < 26:
        return f"Response {chr(ord('A') + index)}"
    return f"Response {index + 1}"


def rotate(items: Sequence[Contribution], by: int) -> list[Contribution]:
    """Rotate left by ``by``, so each critic leads with a different opening."""
    if not items:
        return []
    offset = by % len(items)
    return [*items[offset:], *items[:offset]]


def extract_revised_answer(critique: str) -> str | None:
    """Pull the revised-answer section out of a critique.

    Returns None when the model ignored the heading format, so callers can fall
    back to the whole response rather than showing an empty answer.
    """
    match = REVISED_HEADING.search(critique)
    if match is None:
        return None

    body = critique[match.end() :]
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if ANY_HEADING.match(line):
            return "\n".join(lines[:index]).strip() or None
    return body.strip() or None


def pick_judge(
    providers: Sequence[ProviderConfig], alive: set[str]
) -> ProviderConfig | None:
    """Heaviest surviving provider judges.

    Reuses the registry's ``weight``, which already expresses how much each
    model is trusted, instead of hardcoding a vendor order.
    """
    candidates = [p for p in providers if p.id in alive]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.weight)


# --- Engine ----------------------------------------------------------------


class DebateEngine:
    """Runs the three-round protocol across a set of providers."""

    def __init__(self, service: LLMService | None = None) -> None:
        self._service = service or llm_service

    async def debate(
        self,
        question: str,
        providers: Sequence[ProviderConfig],
    ) -> DebateResult:
        question = question.strip()
        if not question:
            raise DebateError("A debate needs a question.")
        if not providers:
            raise DebateError("No models are enabled for this debate.")

        started = time.perf_counter()
        solo_mode = len(providers) == 1
        labels = {provider.id: label_for(i) for i, provider in enumerate(providers)}
        results: list[LLMResult] = []

        # -- round 1 ---------------------------------------------------------
        opening_round = await self._run_round(
            stage=OPENING,
            providers=providers,
            labels=labels,
            collected=results,
            build_messages=lambda _provider, _index: (
                opening_system(),
                f"The user's question:\n\n{question}",
            ),
        )

        if not opening_round.contributions:
            detail = "; ".join(
                f"{f.model_name}: {f.error}" for f in opening_round.failures
            )
            raise DebateError(f"Every model failed the opening round. {detail}")

        openings = opening_round.contributions
        survivors = [p for p in providers if p.id in {c.model_id for c in openings}]

        # -- round 2 ---------------------------------------------------------
        def build_cross(provider: ProviderConfig, index: int) -> tuple[str, str]:
            if solo_mode:
                return solo_cross_system(), solo_cross_user(question, openings[0])
            return cross_system(), cross_user(question, rotate(openings, index + 1))

        cross_round = await self._run_round(
            stage=CROSS,
            providers=survivors,
            labels=labels,
            collected=results,
            build_messages=build_cross,
        )

        # -- round 3 ---------------------------------------------------------
        judged_pool = cross_round.contributions or openings
        judge = pick_judge(providers, {c.model_id for c in judged_pool})
        if judge is None:  # pragma: no cover — round 1 guarantees a survivor
            raise DebateError("No model available to judge the debate.")

        synthesis_round = DebateRound(stage=SYNTHESIS)
        judge_fell_back = False

        try:
            verdict = await self._service.complete(
                judge,
                system_prompt=synthesis_system(solo_mode=solo_mode),
                user_prompt=synthesis_user(question, openings, cross_round.contributions),
                phase=f"debate_{SYNTHESIS}",
            )
        except Exception as exc:
            # Losing the judge must not lose the debate: fall back to the best
            # revised answer already paid for.
            judge_fell_back = True
            final_answer = self._fallback_answer(judged_pool, judge)
            synthesis_round.failures.append(
                RoundFailure(
                    model_id=judge.id,
                    model_name=judge.name,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            logger.warning(
                "debate_judge_failed",
                extra={"model_id": judge.id, "error": str(exc)},
            )
        else:
            results.append(verdict)
            final_answer = verdict.content.strip()
            synthesis_round.contributions.append(
                Contribution(
                    model_id=judge.id,
                    model_name=judge.name,
                    provider=judge.provider,
                    label=labels.get(judge.id, "Judge"),
                    content=final_answer,
                    latency_ms=verdict.latency_ms,
                    cost_usd=verdict.cost_usd,
                )
            )

        input_tokens = sum(r.input_tokens for r in results)
        output_tokens = sum(r.output_tokens for r in results)
        cost_usd = round(sum(r.cost_usd for r in results), 6)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        logger.info(
            "debate_completed",
            extra={
                "participants": len(openings),
                "judge": judge.id,
                "judge_fell_back": judge_fell_back,
                "elapsed_ms": elapsed_ms,
                "cost_usd": cost_usd,
            },
        )

        return DebateResult(
            question=question,
            rounds=[opening_round, cross_round, synthesis_round],
            final_answer=final_answer,
            judge_model_id=judge.id,
            judge_model_name=judge.name,
            judge_fell_back=judge_fell_back,
            solo_mode=solo_mode,
            elapsed_ms=elapsed_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    async def _run_round(
        self,
        *,
        stage: str,
        providers: Sequence[ProviderConfig],
        labels: dict[str, str],
        collected: list[LLMResult],
        build_messages: Callable[[ProviderConfig, int], tuple[str, str]],
    ) -> DebateRound:
        """Fan one round out, isolating per-provider failures."""
        if not providers:
            return DebateRound(stage=stage)

        order = {provider.id: index for index, provider in enumerate(providers)}

        results, failures = await self._service.fan_out(
            providers,
            build_messages=lambda provider: build_messages(provider, order[provider.id]),
            phase=f"debate_{stage}",
        )
        collected.extend(results)

        contributions = [
            Contribution(
                model_id=result.model_id,
                model_name=result.model_name,
                provider=result.provider,
                label=labels.get(result.model_id, "Response ?"),
                content=result.content.strip(),
                latency_ms=result.latency_ms,
                cost_usd=result.cost_usd,
            )
            for result in results
        ]
        # Keep the transcript in registry order rather than completion order, so
        # the labels a critic saw line up with what the user reads afterwards.
        contributions.sort(key=lambda c: order.get(c.model_id, 0))

        return DebateRound(
            stage=stage,
            contributions=contributions,
            failures=[
                RoundFailure(
                    model_id=failure.model_id,
                    model_name=failure.model_name,
                    error=failure.error,
                )
                for failure in failures
            ],
        )

    @staticmethod
    def _fallback_answer(
        pool: Sequence[Contribution], judge: ProviderConfig
    ) -> str:
        """Best available answer when the judge turn failed."""
        preferred = next((c for c in pool if c.model_id == judge.id), pool[0])
        return extract_revised_answer(preferred.content) or preferred.content


def debate_to_read(result: DebateResult) -> DebateRead:
    """Map the internal result onto the API schema."""
    return DebateRead(
        question=result.question,
        final_answer=result.final_answer,
        judge_model_id=result.judge_model_id,
        judge_model_name=result.judge_model_name,
        judge_fell_back=result.judge_fell_back,
        solo_mode=result.solo_mode,
        elapsed_ms=result.elapsed_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        rounds=[
            DebateRoundRead(
                stage=round_.stage,
                title=round_.title,
                contributions=[
                    DebateContribution(
                        model_id=c.model_id,
                        model_name=c.model_name,
                        provider=c.provider,
                        label=c.label,
                        content=c.content,
                        latency_ms=c.latency_ms,
                        cost_usd=c.cost_usd,
                    )
                    for c in round_.contributions
                ],
                failures=[
                    DebateFailure(
                        model_id=f.model_id,
                        model_name=f.model_name,
                        error=f.error,
                    )
                    for f in round_.failures
                ],
            )
            for round_ in result.rounds
        ],
    )


debate_engine = DebateEngine()

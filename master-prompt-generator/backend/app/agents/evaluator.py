"""AI Judge agent.

Every candidate prompt is scored twice:

  1. Deterministically, by a rule engine that inspects the prompt text for
     structural and safety features. This is reproducible and cannot be
     talked out of a verdict.
  2. By a judge LLM constrained to a strict JSON schema.

The two are blended per metric so the final score is neither purely
subjective nor blind to nuance. When the judge model is unavailable the
deterministic score stands on its own and the run still completes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.models.schemas import (
    METRIC_DEFINITIONS,
    METRIC_KEYS,
    METRIC_WEIGHTS,
    JudgeVerdict,
    RequirementAnalysis,
    RiskLevel,
    SecurityAssessment,
)
from app.services.llm_service import (
    LLMError,
    LLMResult,
    LLMService,
    estimate_tokens,
    llm_service,
)
from app.services.model_registry import UnknownProviderError, model_registry

logger = get_logger(__name__)

DETERMINISTIC_WEIGHT = 0.35
LLM_WEIGHT = 0.65

HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,4}\s+(?P<title>.+?)\s*$", re.MULTILINE)
XML_TAG_PATTERN = re.compile(r"<(?P<tag>[a-z_][a-z0-9_\-]{1,40})>", re.IGNORECASE)
PLACEHOLDER_PATTERN = re.compile(
    r"\b(TODO|TBD|FIXME|XXX|lorem ipsum)\b|\[(insert|your|placeholder)[^\]]*\]",
    re.IGNORECASE,
)

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "role": ("you are", "your role", "act as", "persona", "expertise"),
    "format": (
        "output format", "respond with", "return a", "json", "schema", "xml",
        "markdown", "yaml", "must match",
    ),
    "negative": (
        "do not", "never", "must not", "avoid", "refuse", "under no circumstances",
        "without", "prohibited",
    ),
    "reasoning": (
        "step by step", "step-by-step", "think", "reason", "chain of thought",
        "before answering", "plan", "analyse", "analyze", "evaluate options",
    ),
    "grounding": (
        "only use", "provided context", "cite", "source", "if you do not know",
        "if unknown", "do not fabricate", "do not invent", "ground",
    ),
    "security": (
        "injection", "ignore previous", "untrusted", "pii", "personally identifiable",
        "confidential", "credentials", "secrets", "sanitiz", "redact",
    ),
    "fallback": (
        "if the tool fails", "on failure", "fallback", "error", "timeout",
        "unavailable", "if you cannot", "escalate", "retry",
    ),
    "tooling": (
        "tool", "function call", "api", "parameter", "argument", "invoke", "endpoint",
    ),
    "determinism": (
        "always", "exactly", "consistent", "deterministic", "same format",
        "temperature", "verbatim",
    ),
    "context": (
        "audience", "domain", "stakeholder", "user", "business", "regulat",
        "compliance", "industry",
    ),
    "examples": ("example", "for instance", "e.g.", "sample", "few-shot"),
}

JUDGE_SYSTEM_PROMPT = """You are a deterministic AI Judge scoring production system prompts.

You are adversarial but fair: you reward prompts that will survive contact with
real traffic and penalise prompts that merely read well. Score strictly — a 90+
means the prompt is deployable to production without edits.

Reply with a single JSON object matching exactly this shape:
{
  "prompt_id": "string echoing the id you were given",
  "overall_score": 0-100 number,
  "metrics": { "<metric_key>": 0-100 number, ... },
  "strengths": ["short, concrete"],
  "weaknesses": ["short, concrete"],
  "missing_elements": ["specific elements that should be added"],
  "security_assessment": {
    "injection_risk": "None | Low | Medium | High",
    "pii_leakage_risk": "None | Low | Medium | High",
    "notes": ["short notes"]
  },
  "rationale": "two sentences justifying the overall score"
}

The metrics object MUST contain every metric key listed in the rubric, and nothing else.
Output JSON only. No prose, no code fences."""


@dataclass
class DeterministicSignal:
    scores: dict[str, float]
    findings: list[str]
    missing: list[str]
    injection_risk: RiskLevel
    pii_risk: RiskLevel
    token_count: int
    section_count: int
    has_placeholders: bool


def _count_hits(text: str, keys: Iterable[str]) -> int:
    return sum(1 for key in keys if key in text)


def _scaled(hits: int, target: int, floor: float = 20.0) -> float:
    """Map keyword hits onto a 0-100 score with diminishing returns."""
    if target <= 0:
        return floor
    ratio = min(1.0, hits / target)
    return round(floor + (100.0 - floor) * ratio, 2)


def analyze_deterministically(
    content: str, analysis: Optional[RequirementAnalysis] = None
) -> DeterministicSignal:
    """Score a prompt on observable structural and safety features."""
    lowered = content.lower()
    headings = [match.group("title").strip() for match in HEADING_PATTERN.finditer(content)]
    token_count = estimate_tokens(content)
    word_count = max(1, len(content.split()))
    bullet_lines = sum(
        1 for line in content.splitlines() if line.strip().startswith(("-", "*", "1.", "2."))
    )
    has_placeholders = bool(PLACEHOLDER_PATTERN.search(content))
    has_json_block = "{" in content and "}" in content
    has_xml_tags = bool(XML_TAG_PATTERN.search(content))

    findings: list[str] = []
    missing: list[str] = []
    scores: dict[str, float] = {}

    # --- Clarity & structure ---------------------------------------------
    imperative_lines = sum(
        1
        for line in content.splitlines()
        if line.strip() and line.strip()[0].isalpha() and len(line.split()) <= 40
    )
    clarity = _scaled(min(imperative_lines, 30), 24, floor=30)
    if bullet_lines >= 5:
        clarity = min(100.0, clarity + 6)
        findings.append("Directives are enumerated rather than buried in prose.")
    if has_placeholders:
        clarity -= 25
        missing.append("Unresolved placeholders (TODO/TBD/[insert ...]) remain in the prompt.")
    scores["instruction_clarity"] = max(0.0, round(clarity, 2))

    role_hits = _count_hits(lowered, _KEYWORDS["role"])
    scores["role_definition"] = _scaled(role_hits, 3, floor=25)
    if role_hits == 0:
        missing.append("An explicit role or persona statement.")
    else:
        findings.append("Role and expertise boundaries are stated.")

    format_hits = _count_hits(lowered, _KEYWORDS["format"])
    format_score = _scaled(format_hits, 4, floor=20)
    if has_json_block or has_xml_tags:
        format_score = min(100.0, format_score + 15)
        findings.append("Contains a machine-checkable output contract.")
    else:
        missing.append("A concrete output schema or template block.")
    scores["output_formatting"] = round(format_score, 2)

    negative_hits = _count_hits(lowered, _KEYWORDS["negative"])
    scores["constraints_completeness"] = _scaled(negative_hits, 5, floor=15)
    if negative_hits < 2:
        missing.append("Explicit negative constraints (what the model must not do).")

    section_score = _scaled(len(headings), 6, floor=20)
    if analysis and analysis.required_sections:
        required = {section.lower() for section in analysis.required_sections}
        present = {heading.lower() for heading in headings}
        covered = sum(
            1
            for requirement in required
            if any(requirement in heading or heading in requirement for heading in present)
        )
        coverage = covered / max(1, len(required))
        section_score = round(20 + 80 * coverage, 2)
        if coverage < 1.0:
            outstanding = [
                section
                for section in analysis.required_sections
                if not any(
                    section.lower() in heading or heading in section.lower()
                    for heading in present
                )
            ]
            missing.extend(f"Required section '{section}'." for section in outstanding[:4])
    scores["structural_organization"] = section_score

    # --- Cognitive quality -----------------------------------------------
    reasoning_hits = _count_hits(lowered, _KEYWORDS["reasoning"])
    reasoning_score = _scaled(reasoning_hits, 4, floor=20)
    if analysis and analysis.reasoning_strategy == "direct":
        reasoning_score = max(reasoning_score, 70.0)
    elif reasoning_hits == 0:
        missing.append("A reasoning scaffold appropriate to the task complexity.")
    scores["reasoning_quality"] = reasoning_score

    scores["context_awareness"] = _scaled(
        _count_hits(lowered, _KEYWORDS["context"]), 4, floor=20
    )

    grounding_hits = _count_hits(lowered, _KEYWORDS["grounding"])
    scores["hallucination_prevention"] = _scaled(grounding_hits, 4, floor=15)
    if grounding_hits == 0:
        missing.append("Grounding rules and an abstention path for unknown answers.")
    else:
        findings.append("Defines grounding duties and an abstention path.")

    fallback_hits = _count_hits(lowered, _KEYWORDS["fallback"])
    scores["adaptability"] = _scaled(fallback_hits, 3, floor=20)
    if fallback_hits == 0:
        missing.append("Explicit fallback instructions on failure or missing context.")

    example_hits = _count_hits(lowered, _KEYWORDS["examples"])
    scores["example_quality"] = _scaled(example_hits, 3, floor=25)

    # --- Production readiness --------------------------------------------
    security_hits = _count_hits(lowered, _KEYWORDS["security"])
    scores["security_guardrails"] = _scaled(security_hits, 4, floor=10)
    injection_risk = RiskLevel.HIGH
    if security_hits >= 4:
        injection_risk = RiskLevel.NONE
    elif security_hits >= 2:
        injection_risk = RiskLevel.LOW
    elif security_hits >= 1:
        injection_risk = RiskLevel.MEDIUM
    if security_hits == 0:
        missing.append("Prompt-injection resistance and untrusted-input handling rules.")
    else:
        findings.append("Carries explicit security guardrails.")

    pii_mentions = any(
        term in lowered for term in ("pii", "personally identifiable", "redact", "anonymi")
    )
    pii_risk = RiskLevel.NONE if pii_mentions else RiskLevel.LOW

    # Token efficiency: reward information density, penalise sprawl.
    if word_count <= 120:
        efficiency = 55.0  # too thin to be production-ready
    elif word_count <= 900:
        efficiency = 100.0 - (word_count - 120) / 900 * 18
    else:
        efficiency = max(25.0, 82.0 - (word_count - 900) / 90)
    duplicate_penalty = _duplicate_line_ratio(content) * 40
    scores["token_efficiency"] = max(0.0, round(efficiency - duplicate_penalty, 2))

    determinism_hits = _count_hits(lowered, _KEYWORDS["determinism"])
    determinism = _scaled(determinism_hits, 4, floor=25)
    if has_json_block:
        determinism = min(100.0, determinism + 10)
    scores["determinism"] = round(determinism, 2)

    scores["tool_calling_accuracy"] = _scaled(
        _count_hits(lowered, _KEYWORDS["tooling"]), 4, floor=25
    )

    maintainability = _scaled(len(headings), 6, floor=25)
    if has_placeholders:
        maintainability -= 20
    scores["maintainability"] = max(0.0, round(maintainability, 2))

    return DeterministicSignal(
        scores={key: scores.get(key, 50.0) for key in METRIC_KEYS},
        findings=findings,
        missing=missing,
        injection_risk=injection_risk,
        pii_risk=pii_risk,
        token_count=token_count,
        section_count=len(headings),
        has_placeholders=has_placeholders,
    )


def _duplicate_line_ratio(content: str) -> float:
    lines = [line.strip().lower() for line in content.splitlines() if len(line.strip()) > 25]
    if not lines:
        return 0.0
    return 1 - (len(set(lines)) / len(lines))


def weighted_overall(metrics: dict[str, float]) -> float:
    present = {key: value for key, value in metrics.items() if key in METRIC_WEIGHTS}
    if not present:
        return 0.0
    total_weight = sum(METRIC_WEIGHTS[key] for key in present)
    return round(sum(METRIC_WEIGHTS[key] * value for key, value in present.items()) / total_weight, 2)


def _rubric_block() -> str:
    return "\n".join(
        f"- {metric.key} — {metric.label} (weight {metric.weight:.0%}): {metric.description}"
        for metric in METRIC_DEFINITIONS
    )


class PromptEvaluator:
    """Scores candidate prompts against the fifteen-criterion rubric."""

    def __init__(self, service: Optional[LLMService] = None) -> None:
        self._llm = service or llm_service

    async def evaluate(
        self,
        *,
        prompt_id: str,
        content: str,
        business_problem: str,
        analysis: Optional[RequirementAnalysis] = None,
    ) -> tuple[JudgeVerdict, Optional[LLMResult]]:
        signal = analyze_deterministically(content, analysis)
        deterministic_verdict = self._verdict_from_signal(prompt_id, signal)

        try:
            provider = model_registry.get(settings.judge_model_id)
        except UnknownProviderError:
            enabled = model_registry.enabled()
            provider = enabled[0] if enabled else None

        if provider is None:
            return deterministic_verdict, None

        user_prompt = (
            f"# Prompt Id\n{prompt_id}\n\n"
            f"# Original Business Problem\n{business_problem.strip()}\n\n"
            f"# Scoring Rubric\n{_rubric_block()}\n\n"
            "# Static Analysis Findings (already verified by a rule engine)\n"
            f"- Estimated tokens: {signal.token_count}\n"
            f"- Section headings detected: {signal.section_count}\n"
            f"- Unresolved placeholders present: {signal.has_placeholders}\n"
            f"- Detected gaps: {'; '.join(signal.missing[:6]) or 'none'}\n\n"
            "# Candidate Prompt Under Review\n"
            "<<<CANDIDATE_PROMPT\n"
            f"{content}\n"
            "CANDIDATE_PROMPT\n\n"
            "Score the candidate prompt and return the verdict JSON."
        )

        try:
            payload, result = await self._llm.complete_json(
                provider,
                system_prompt=JUDGE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                phase="evaluation",
                max_tokens=settings.judge_max_tokens,
                timeout=settings.judge_timeout_seconds,
            )
            payload.setdefault("prompt_id", prompt_id)
            llm_verdict = JudgeVerdict.model_validate(payload)
        except (LLMError, ValueError) as exc:
            logger.warning(
                "judge_failed_using_deterministic",
                extra={"prompt_id": prompt_id, "error": str(exc)},
            )
            return deterministic_verdict, None

        return self._blend(prompt_id, signal, llm_verdict), result

    def _verdict_from_signal(
        self, prompt_id: str, signal: DeterministicSignal
    ) -> JudgeVerdict:
        return JudgeVerdict(
            prompt_id=prompt_id,
            overall_score=weighted_overall(signal.scores),
            metrics=signal.scores,
            strengths=signal.findings[:6],
            weaknesses=(
                ["Unresolved placeholders remain"] if signal.has_placeholders else []
            ),
            missing_elements=signal.missing[:8],
            security_assessment=SecurityAssessment(
                injection_risk=signal.injection_risk,
                pii_leakage_risk=signal.pii_risk,
                notes=["Scored by the deterministic rule engine."],
            ),
            rationale="Deterministic structural analysis only; judge model unavailable.",
        )

    def _blend(
        self, prompt_id: str, signal: DeterministicSignal, verdict: JudgeVerdict
    ) -> JudgeVerdict:
        blended: dict[str, float] = {}
        for key in METRIC_KEYS:
            deterministic = signal.scores.get(key, 50.0)
            judged = verdict.metrics.get(key)
            if judged is None:
                blended[key] = round(deterministic, 2)
            else:
                blended[key] = round(
                    LLM_WEIGHT * judged + DETERMINISTIC_WEIGHT * deterministic, 2
                )

        # Hard gate: placeholder text is never production ready.
        if signal.has_placeholders:
            blended["maintainability"] = min(blended["maintainability"], 45.0)
            blended["instruction_clarity"] = min(blended["instruction_clarity"], 55.0)

        merged_missing = list(
            dict.fromkeys([*verdict.missing_elements, *signal.missing])
        )
        security = verdict.security_assessment
        if _risk_rank(signal.injection_risk) > _risk_rank(security.injection_risk):
            security = security.model_copy(
                update={"injection_risk": signal.injection_risk}
            )

        return JudgeVerdict(
            prompt_id=prompt_id,
            overall_score=weighted_overall(blended),
            metrics=blended,
            strengths=list(dict.fromkeys([*verdict.strengths, *signal.findings]))[:8],
            weaknesses=verdict.weaknesses[:8],
            missing_elements=merged_missing[:10],
            security_assessment=security,
            rationale=verdict.rationale,
        )


def _risk_rank(level: RiskLevel) -> int:
    return {
        RiskLevel.NONE: 0,
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
    }[level]


def verdict_to_payload(verdict: JudgeVerdict) -> dict[str, Any]:
    return verdict.model_dump(mode="json")


prompt_evaluator = PromptEvaluator()

"""Requirement Analyzer agent.

Turns a raw business brief into (a) a structured requirement analysis and
(b) model-specific seed meta-prompts that steer each provider toward the
style it handles best.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.logging import get_logger
from app.models.schemas import (
    METRIC_DEFINITIONS,
    ProviderConfig,
    RequirementAnalysis,
    RunCreate,
)
from app.services.llm_service import LLMError, LLMResult, LLMService, llm_service
from app.services.model_registry import UnknownProviderError, model_registry

logger = get_logger(__name__)

ANALYZER_SYSTEM_PROMPT = """You are a Principal Prompt Engineer performing requirement analysis.

Given a business brief you determine what a production system prompt must contain
for the task to succeed reliably. You never write the prompt itself — you produce
the specification another agent will build from.

Reply with a single JSON object matching exactly this shape:
{
  "task_type": "string, e.g. 'structured extraction' or 'multi-turn support agent'",
  "complexity": "low | medium | high | extreme",
  "reasoning_strategy": "direct | chain_of_thought | tree_of_thought",
  "domain_context": "2-4 sentences on domain terminology, regulations and norms",
  "success_criteria": ["measurable criteria the prompt output must satisfy"],
  "risk_factors": ["failure modes: hallucination surfaces, injection vectors, PII"],
  "required_sections": ["ordered section headings the master prompt must contain"],
  "recommended_output_format": "markdown | json | xml | yaml",
  "tone": "short description of register and voice",
  "notes": "anything the generator must not miss"
}

Rules:
- required_sections must contain between 5 and 10 entries in execution order.
- success_criteria and risk_factors must each contain at least 3 entries.
- Output JSON only. No prose, no code fences."""

GENERATOR_SYSTEM_PROMPT = """You are a Principal Prompt Engineer authoring a production system prompt.

You write the final artefact that will be deployed verbatim into an LLM
application. Your output is the prompt itself — never commentary about it.

Hard requirements:
- Use Markdown headings (##) for every section named in the specification.
- Define the role, capabilities and authority boundaries explicitly.
- State the output contract precisely, including a schema when structured output is required.
- Include explicit negative constraints and refusal conditions.
- Include prompt-injection resistance and PII handling rules.
- Include a fallback path for tool failures, missing context and low confidence.
- Be token-efficient: no filler, no restating the same instruction twice.
- Never emit placeholders such as TODO, TBD or [insert ...].

Output the prompt only. Do not wrap it in code fences and do not add an explanation."""

# Per-provider steering appended to the seed prompt. Keeps the fan-out diverse
# so the consensus engine has genuinely different material to merge.
PROVIDER_STYLE_HINTS: dict[str, str] = {
    "openai": (
        "Optimise for instruction-following density: numbered directives, tight "
        "imperative phrasing, and an explicit JSON schema block where structured "
        "output is required."
    ),
    "anthropic": (
        "Optimise for structural clarity: XML-style delimiters around volatile "
        "context, an explicit reasoning scaffold before the answer section, and "
        "carefully stated boundary conditions."
    ),
    "google": (
        "Optimise for coverage and grounding: enumerate context sources, define "
        "citation duties, and specify behaviour for ambiguous or conflicting inputs."
    ),
}

DEFAULT_STYLE_HINT = (
    "Optimise for clarity, determinism and explicit constraint coverage."
)

FALLBACK_SECTIONS = [
    "Role & Objective",
    "Context",
    "Instructions",
    "Reasoning Process",
    "Output Format",
    "Constraints & Guardrails",
    "Failure Handling",
    "Examples",
]


def _rubric_digest() -> str:
    lines = [
        f"- {metric.label} ({metric.weight:.0%}): {metric.description}"
        for metric in METRIC_DEFINITIONS
    ]
    return "\n".join(lines)


def _bullet_list(items: list[str], empty: str = "None specified.") -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty


def build_brief(request: RunCreate) -> str:
    """Render the user's submission into a stable analyst-facing brief."""
    return (
        f"# Business Problem\n{request.business_problem.strip()}\n\n"
        f"# Target Domain\n{request.target_domain.strip()}\n\n"
        f"# Intended Audience\n{request.audience or 'Not specified.'}\n\n"
        f"# Desired Output Format\n{request.output_format or 'Not specified.'}\n\n"
        f"# Constraints\n{_bullet_list(request.constraints)}\n\n"
        f"# Prompt Requirements\n{_bullet_list(request.requirements)}"
    )


def heuristic_analysis(request: RunCreate) -> RequirementAnalysis:
    """Deterministic analysis used when the analyzer model is unavailable.

    The pipeline must degrade rather than fail: a slightly less tailored
    specification is far better than a dead run.
    """
    brief = f"{request.business_problem} {' '.join(request.requirements)}".lower()
    length = len(request.business_problem.split())

    complexity = "low"
    if length > 60 or len(request.requirements) > 3:
        complexity = "medium"
    if length > 180 or len(request.requirements) > 7 or len(request.constraints) > 7:
        complexity = "high"
    if length > 400:
        complexity = "extreme"

    strategy = "chain_of_thought"
    if complexity == "low":
        strategy = "direct"
    elif complexity == "extreme" or "trade-off" in brief or "strategy" in brief:
        strategy = "tree_of_thought"

    output_format = (request.output_format or "").strip().lower()
    if output_format not in {"markdown", "json", "xml", "yaml"}:
        output_format = "json" if "json" in brief or "api" in brief else "markdown"

    risks = ["Ungrounded assertions when source context is thin"]
    if any(term in brief for term in ("customer", "user data", "pii", "personal")):
        risks.append("Exposure of personally identifiable information")
    else:
        risks.append("Leakage of internal system instructions")
    risks.append("Prompt injection through untrusted input passed into the prompt")

    return RequirementAnalysis(
        task_type=f"{request.target_domain.strip()} prompt engineering",
        complexity=complexity,  # type: ignore[arg-type]
        reasoning_strategy=strategy,  # type: ignore[arg-type]
        domain_context=(
            f"Operates in the {request.target_domain.strip()} domain for "
            f"{request.audience or 'a professional audience'}."
        ),
        success_criteria=(
            request.requirements[:5]
            or [
                "Output satisfies the stated business problem without follow-up",
                "Response conforms to the declared output contract",
                "No fabricated facts or unsupported claims",
            ]
        ),
        risk_factors=risks,
        required_sections=FALLBACK_SECTIONS,
        recommended_output_format=output_format,
        tone="precise, professional",
        notes="Generated by the deterministic analyzer fallback.",
    )


class RequirementAnalyzer:
    """Produces the requirement specification and the per-model seed prompts."""

    def __init__(self, service: Optional[LLMService] = None) -> None:
        self._llm = service or llm_service

    async def analyze(
        self, request: RunCreate
    ) -> tuple[RequirementAnalysis, Optional[LLMResult]]:
        from app.core.config import settings

        brief = build_brief(request)
        try:
            provider = model_registry.get(settings.analyzer_model_id)
        except UnknownProviderError:
            enabled = model_registry.enabled()
            provider = enabled[0] if enabled else None

        if provider is None:
            logger.warning("analyzer_no_provider_using_heuristics")
            return heuristic_analysis(request), None

        user_prompt = (
            f"{brief}\n\n"
            "# Evaluation Rubric The Final Prompt Will Face\n"
            f"{_rubric_digest()}\n\n"
            "Analyse the brief and return the specification JSON."
        )

        try:
            payload, result = await self._llm.complete_json(
                provider,
                system_prompt=ANALYZER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                phase="analysis",
            )
        except (LLMError, ValueError) as exc:
            logger.warning(
                "analyzer_failed_using_heuristics",
                extra={"model_id": provider.id, "error": str(exc)},
            )
            return heuristic_analysis(request), None

        try:
            analysis = RequirementAnalysis.model_validate(payload)
        except ValueError as exc:
            logger.warning(
                "analyzer_schema_invalid_using_heuristics",
                extra={"error": str(exc)},
            )
            return heuristic_analysis(request), result

        if len(analysis.required_sections) < 5:
            analysis.required_sections = FALLBACK_SECTIONS
        return analysis, result

    def build_seed_prompt(
        self,
        request: RunCreate,
        analysis: RequirementAnalysis,
        provider: ProviderConfig,
    ) -> str:
        """Compose the model-specific meta-prompt used during fan-out."""
        style = PROVIDER_STYLE_HINTS.get(
            provider.provider.strip().lower(), DEFAULT_STYLE_HINT
        )
        sections = "\n".join(
            f"{index}. {section}"
            for index, section in enumerate(analysis.required_sections, start=1)
        )
        return (
            f"{build_brief(request)}\n\n"
            "# Requirement Analysis\n"
            f"- Task type: {analysis.task_type}\n"
            f"- Complexity: {analysis.complexity}\n"
            f"- Reasoning strategy: {analysis.reasoning_strategy}\n"
            f"- Recommended output format: {analysis.recommended_output_format}\n"
            f"- Tone: {analysis.tone}\n"
            f"- Domain context: {analysis.domain_context}\n\n"
            "# Success Criteria\n"
            f"{_bullet_list(analysis.success_criteria)}\n\n"
            "# Risk Factors To Neutralise\n"
            f"{_bullet_list(analysis.risk_factors)}\n\n"
            "# Required Sections (use these exact '## ' headings, in this order)\n"
            f"{sections}\n\n"
            "# Model-Specific Guidance\n"
            f"{style}\n\n"
            "# Scoring Rubric Your Output Will Be Graded Against\n"
            f"{_rubric_digest()}\n\n"
            "Write the complete production system prompt now."
        )

    def seed_prompts(
        self,
        request: RunCreate,
        analysis: RequirementAnalysis,
        providers: list[ProviderConfig],
    ) -> dict[str, str]:
        return {
            provider.id: self.build_seed_prompt(request, analysis, provider)
            for provider in providers
        }

    @staticmethod
    def generator_system_prompt(analysis: RequirementAnalysis) -> str:
        return (
            f"{GENERATOR_SYSTEM_PROMPT}\n\n"
            f"Reasoning strategy to encode: {analysis.reasoning_strategy}.\n"
            f"Target output format: {analysis.recommended_output_format}."
        )


def analysis_to_payload(analysis: RequirementAnalysis) -> dict[str, Any]:
    return analysis.model_dump(mode="json")


requirement_analyzer = RequirementAnalyzer()

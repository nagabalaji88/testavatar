"""Transport schemas: API request/response bodies and agent contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ---------------------------------------------------------------------------
# Evaluation rubric
# ---------------------------------------------------------------------------


class MetricCategory(StrEnum):
    CLARITY_STRUCTURE = "clarity_structure"
    COGNITIVE_QUALITY = "cognitive_quality"
    PRODUCTION_READINESS = "production_readiness"


class MetricDefinition(BaseModel):
    key: str
    label: str
    category: MetricCategory
    weight: float
    description: str


# Fifteen quantitative criteria, weighted to sum to 1.0.
METRIC_DEFINITIONS: list[MetricDefinition] = [
    MetricDefinition(
        key="instruction_clarity",
        label="Instruction Clarity",
        category=MetricCategory.CLARITY_STRUCTURE,
        weight=0.09,
        description="Instructions are unambiguous, actionable and free of contradiction.",
    ),
    MetricDefinition(
        key="role_definition",
        label="Role Definition",
        category=MetricCategory.CLARITY_STRUCTURE,
        weight=0.07,
        description="The persona, expertise and authority boundaries are explicit.",
    ),
    MetricDefinition(
        key="output_formatting",
        label="Output Formatting",
        category=MetricCategory.CLARITY_STRUCTURE,
        weight=0.08,
        description="A machine-checkable output contract (JSON/XML/schema) is specified.",
    ),
    MetricDefinition(
        key="constraints_completeness",
        label="Constraints Completeness",
        category=MetricCategory.CLARITY_STRUCTURE,
        weight=0.07,
        description="Positive and negative constraints cover the stated requirements.",
    ),
    MetricDefinition(
        key="structural_organization",
        label="Structural Organization",
        category=MetricCategory.CLARITY_STRUCTURE,
        weight=0.05,
        description="Sections are ordered and delimited so models can attend reliably.",
    ),
    MetricDefinition(
        key="reasoning_quality",
        label="Reasoning Depth",
        category=MetricCategory.COGNITIVE_QUALITY,
        weight=0.08,
        description="Chain-of-thought or tree-of-thought scaffolding matches task difficulty.",
    ),
    MetricDefinition(
        key="context_awareness",
        label="Context Awareness",
        category=MetricCategory.COGNITIVE_QUALITY,
        weight=0.06,
        description="Domain, audience and upstream/downstream systems are accounted for.",
    ),
    MetricDefinition(
        key="hallucination_prevention",
        label="Hallucination Prevention",
        category=MetricCategory.COGNITIVE_QUALITY,
        weight=0.08,
        description="Grounding rules, citation duties and abstention paths are defined.",
    ),
    MetricDefinition(
        key="adaptability",
        label="Adaptability",
        category=MetricCategory.COGNITIVE_QUALITY,
        weight=0.04,
        description="Handles edge cases and degraded inputs without rewriting the prompt.",
    ),
    MetricDefinition(
        key="example_quality",
        label="Example Quality",
        category=MetricCategory.COGNITIVE_QUALITY,
        weight=0.05,
        description="Few-shot exemplars are representative, minimal and correctly labelled.",
    ),
    MetricDefinition(
        key="security_guardrails",
        label="Security Guardrails",
        category=MetricCategory.PRODUCTION_READINESS,
        weight=0.09,
        description="Injection resistance, PII handling and refusal boundaries are stated.",
    ),
    MetricDefinition(
        key="token_efficiency",
        label="Token Efficiency",
        category=MetricCategory.PRODUCTION_READINESS,
        weight=0.06,
        description="Signal per token is high; no redundant or decorative prose.",
    ),
    MetricDefinition(
        key="determinism",
        label="Determinism",
        category=MetricCategory.PRODUCTION_READINESS,
        weight=0.06,
        description="Repeated executions converge on the same shape and semantics.",
    ),
    MetricDefinition(
        key="tool_calling_accuracy",
        label="Tool / Function Calling",
        category=MetricCategory.PRODUCTION_READINESS,
        weight=0.06,
        description="Tool contracts, argument types and failure handling are precise.",
    ),
    MetricDefinition(
        key="maintainability",
        label="Maintainability",
        category=MetricCategory.PRODUCTION_READINESS,
        weight=0.06,
        description="Versionable, modular and safe for another engineer to extend.",
    ),
]

METRIC_KEYS: list[str] = [metric.key for metric in METRIC_DEFINITIONS]
METRIC_WEIGHTS: dict[str, float] = {m.key: m.weight for m in METRIC_DEFINITIONS}
METRIC_BY_KEY: dict[str, MetricDefinition] = {m.key: m for m in METRIC_DEFINITIONS}


class RiskLevel(StrEnum):
    NONE = "None"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class SecurityAssessment(BaseModel):
    injection_risk: RiskLevel = RiskLevel.LOW
    pii_leakage_risk: RiskLevel = RiskLevel.NONE
    notes: list[str] = Field(default_factory=list)


class JudgeVerdict(BaseModel):
    """Structured response contract enforced on the AI Judge agent."""

    model_config = ConfigDict(populate_by_name=True)

    prompt_id: str
    overall_score: float = Field(ge=0, le=100)
    metrics: dict[str, float] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    missing_elements: list[str] = Field(default_factory=list)
    security_assessment: SecurityAssessment = Field(default_factory=SecurityAssessment)
    rationale: str | None = None

    @field_validator("metrics")
    @classmethod
    def _clamp_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        return {
            key: max(0.0, min(100.0, float(score)))
            for key, score in value.items()
            if key in METRIC_WEIGHTS
        }

    def weighted_score(self) -> float:
        """Recompute the overall score from metrics so it can never drift."""
        present = {k: v for k, v in self.metrics.items() if k in METRIC_WEIGHTS}
        if not present:
            return self.overall_score
        total_weight = sum(METRIC_WEIGHTS[k] for k in present)
        weighted = sum(METRIC_WEIGHTS[k] * v for k, v in present.items())
        return round(weighted / total_weight, 2)


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------


class ProviderConfig(BaseModel):
    id: str
    name: str
    provider: str
    model_key: str
    max_tokens: int = Field(gt=0, le=200_000)
    cost_per_1k_input: float = Field(ge=0)
    cost_per_1k_output: float = Field(ge=0)
    enabled: bool = True
    temperature: float = Field(default=0.4, ge=0, le=2)
    supports_json_mode: bool = True
    api_base: str | None = None
    weight: float = Field(default=1.0, gt=0)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            (input_tokens / 1000) * self.cost_per_1k_input
            + (output_tokens / 1000) * self.cost_per_1k_output,
            6,
        )


class ProviderRegistryConfig(BaseModel):
    version: str = "1.0"
    providers: list[ProviderConfig]

    @field_validator("providers")
    @classmethod
    def _unique_ids(cls, value: list[ProviderConfig]) -> list[ProviderConfig]:
        seen: set[str] = set()
        for provider in value:
            if provider.id in seen:
                raise ValueError(f"Duplicate provider id: {provider.id}")
            seen.add(provider.id)
        return value


class ProviderToggle(BaseModel):
    enabled: bool


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str | None = None
    role: Literal["viewer", "engineer", "admin"] = "engineer"


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunCreate(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    business_problem: str = Field(min_length=20, max_length=20_000)
    target_domain: str = Field(min_length=2, max_length=160)
    constraints: list[str] = Field(default_factory=list, max_length=40)
    requirements: list[str] = Field(default_factory=list, max_length=40)
    audience: str | None = Field(default=None, max_length=240)
    output_format: str | None = Field(default=None, max_length=64)
    model_ids: list[str] = Field(default_factory=list, max_length=12)


class RequirementAnalysis(BaseModel):
    """Output contract of the Requirement Analyzer agent."""

    task_type: str
    complexity: Literal["low", "medium", "high", "extreme"] = "medium"
    reasoning_strategy: Literal["direct", "chain_of_thought", "tree_of_thought"] = (
        "chain_of_thought"
    )
    domain_context: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    required_sections: list[str] = Field(default_factory=list)
    recommended_output_format: str = "markdown"
    tone: str = "precise, professional"
    notes: str | None = None


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    model_id: str
    model_name: str
    provider: str
    status: str
    content: str | None
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    overall_score: float | None
    metrics: dict[str, float] | None
    evaluation: dict[str, Any] | None


class SectionProvenance(BaseModel):
    section: str
    source_model_id: str
    source_model_name: str
    score: float
    merged_from: list[str] = Field(default_factory=list)
    strategy: Literal["adopted", "merged", "synthesized", "deduplicated"] = "adopted"


class ConflictRecord(BaseModel):
    section: str
    kind: Literal["semantic", "syntactic", "structural"]
    description: str
    competing_models: list[str]
    resolution: str
    winner_model_id: str | None = None


class OptimizationReport(BaseModel):
    original_tokens: int
    optimized_tokens: int
    tokens_saved: int
    compression_ratio: float
    removed_duplicate_lines: int
    collapsed_whitespace_blocks: int
    normalized_headings: int


class ConsensusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content: str
    overall_score: float | None
    metrics: dict[str, float] | None
    evaluation: dict[str, Any] | None
    section_provenance: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    optimization_report: dict[str, Any] | None
    token_count: int
    tokens_saved: int
    improvement_over_best: float | None


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    target_domain: str
    status: str
    total_cost_usd: float
    duration_ms: int | None
    created_at: datetime
    completed_at: datetime | None


class RunDetail(RunSummary):
    business_problem: str
    constraints: list[str]
    requirements: list[str]
    audience: str | None
    output_format: str | None
    selected_model_ids: list[str]
    analysis: dict[str, Any] | None
    error: str | None
    total_input_tokens: int
    total_output_tokens: int
    trace_id: str | None
    candidates: list[CandidateRead] = Field(default_factory=list)
    consensus: ConsensusRead | None = None


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: str
    task_id: str | None = None
    websocket_url: str


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)
    min_score: float = Field(default=0.0, ge=0, le=1)


class SemanticSearchHit(BaseModel):
    run_id: str
    title: str
    score: float
    similarity: float
    excerpt: str
    target_domain: str


class ExportFormat(StrEnum):
    MARKDOWN = "markdown"
    JSON = "json"
    YAML = "yaml"
    XML = "xml"
    PYTHON = "python"
    TYPESCRIPT = "typescript"


class ExportRequest(BaseModel):
    format: ExportFormat = ExportFormat.MARKDOWN
    include_evaluation: bool = True


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    dependencies: dict[str, str]


# ---------------------------------------------------------------------------
# Debate
# ---------------------------------------------------------------------------


class DebateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    provider_ids: list[str] | None = None


class DebateContribution(BaseModel):
    model_id: str
    model_name: str
    provider: str
    label: str
    content: str
    latency_ms: int
    cost_usd: float


class DebateFailure(BaseModel):
    model_id: str
    model_name: str
    error: str


class DebateRoundRead(BaseModel):
    stage: str
    title: str
    contributions: list[DebateContribution]
    failures: list[DebateFailure]


class DebateRead(BaseModel):
    question: str
    final_answer: str
    judge_model_id: str
    judge_model_name: str
    judge_fell_back: bool
    solo_mode: bool
    elapsed_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    rounds: list[DebateRoundRead]

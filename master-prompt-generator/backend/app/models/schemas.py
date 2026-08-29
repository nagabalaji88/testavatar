"""Transport schemas: API request/response bodies and agent contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ---------------------------------------------------------------------------
# Evaluation rubric
# ---------------------------------------------------------------------------


class MetricCategory(str, Enum):
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


class RiskLevel(str, Enum):
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
    rationale: Optional[str] = None

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
    api_base: Optional[str] = None
    # Names the environment variable holding this model's credential, e.g.
    # "OLLAMA_CLOUD_API_KEY". This is the supported way to give one specific
    # registry entry its own key: models.json is tracked in git, .env is not,
    # so the secret never lands in a commit.
    api_key_env: Optional[str] = None
    # Literal inline credential. Only safe when models.json is mounted from
    # outside the repository (a Compose volume, a secrets mount); anything
    # written here in the checked-in file will be committed. api_key_env wins
    # when both are set.
    api_key: Optional[str] = None
    weight: float = Field(default=1.0, gt=0)

    @field_validator("api_base")
    @classmethod
    def _safe_api_base(cls, value: Optional[str]) -> Optional[str]:
        """Reject endpoints that would turn the registry into an SSRF tool."""
        if not value:
            return value
        from app.core.net import UnsafeEndpointError, validate_api_base

        try:
            # resolve=False: a field validator runs on the event loop, and
            # getaddrinfo blocks it. The admin write path awaits the resolving
            # form, so a value arriving from a request still gets the full
            # check -- just not from here.
            return validate_api_base(value, resolve=False)
        except UnsafeEndpointError as exc:
            raise ValueError(str(exc)) from exc

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            (input_tokens / 1000) * self.cost_per_1k_input
            + (output_tokens / 1000) * self.cost_per_1k_output,
            6,
        )


class StreamTicket(BaseModel):
    """Single-use credential for one run's websocket stream."""

    ticket: str
    expires_in: int


class ProviderPublic(BaseModel):
    """A registry entry as the API is allowed to return it.

    ProviderConfig carries the inline api_key, and GET /models is readable by
    any authenticated principal -- with open registration on, that is anyone
    who can reach the service. Serialising the model directly hands the
    credential to every caller, so responses are narrowed to this shape.

    api_key_env survives: the *name* of a variable is not a secret, and an
    operator debugging a misconfigured entry needs to see which one it reads.
    """

    id: str
    name: str
    provider: str
    model_key: str
    max_tokens: int
    cost_per_1k_input: float
    cost_per_1k_output: float
    enabled: bool
    temperature: float
    supports_json_mode: bool
    api_base: Optional[str] = None
    api_key_env: Optional[str] = None
    weight: float

    # --- credential state -------------------------------------------------
    # Whether this entry can actually be called right now. "Enabled" only says
    # an operator wants it; without a key it is dispatched, fails its whole
    # retry ladder and is dropped, which from the UI looks like a model that
    # silently did nothing.
    credential_available: bool = True
    # The variable the credential is read from -- the name, never the value --
    # so the UI can say which one to set instead of only that one is missing.
    credential_env_var: Optional[str] = None
    # Served from your own hardware, so no credential applies at all. This is
    # not the same as "credential missing" and should not read as a problem.
    is_local_runtime: bool = False
    # Which credential family would fix this entry, so the UI can link a
    # keyless model straight to the field that supplies it.
    credential_family: Optional[str] = None
    # Which source answered: entry_env | entry_inline | database | environment.
    # "The key is set" stops being actionable once there are two places it can
    # come from -- an operator editing the wrong one sees no effect.
    credential_source: Optional[str] = None


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
# Provider credentials
# ---------------------------------------------------------------------------


class CredentialStatus(BaseModel):
    """What is known about one family's key, minus the key.

    Deliberately has no field that could carry the value. `last4` is the whole
    of what is disclosed, and it exists because "configured: true" does not
    answer the question an operator actually has -- whether the key in place is
    the one they think it is.
    """

    family: str
    label: str
    env_var: str
    console_url: Optional[str] = None
    configured: bool = False
    last4: Optional[str] = None
    # database | environment -- which source is currently winning. The database
    # takes precedence, so an operator who sets a key here can see that it is
    # now the effective one even though the old variable is still exported.
    source: Optional[str] = None
    # True when a stored row exists but the encryption key can no longer read
    # it, which needs a re-entry rather than looking like an absent key.
    needs_reentry: bool = False
    updated_at: Optional[datetime] = None
    # False for a variable a registry entry names through api_key_env. Those
    # are addressed by variable name rather than by family, so there is no
    # family row to store a key against -- the environment is the only place
    # they can be set. Listed all the same, because an operator who exports one
    # needs to see it here rather than infer it from the Models page.
    editable: bool = True
    # How many enabled registry entries this key unblocks, so the UI can say
    # what setting it would actually achieve.
    model_count: int = 0


class CredentialWrite(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)

    @field_validator("api_key")
    @classmethod
    def _trimmed_and_present(cls, value: str) -> str:
        # Copy-paste from a provider console routinely brings whitespace or a
        # newline along; sent verbatim these produce a malformed auth header
        # and a 401 that reads as a wrong key.
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("api_key must not be blank")
        return trimmed


class CredentialTestResult(BaseModel):
    """Outcome of proving a key against the provider before relying on it."""

    family: str
    ok: bool
    detail: str
    model_count: int = 0


# ---------------------------------------------------------------------------
# Live model discovery
# ---------------------------------------------------------------------------


class DiscoveredModelPublic(BaseModel):
    family: str
    provider_label: str
    model_key: str
    remote_id: str
    display_name: str
    cost_per_1k_input: Optional[float] = None
    cost_per_1k_output: Optional[float] = None
    max_tokens: Optional[int] = None
    supports_json_mode: bool = True
    # Set by the API, not the provider: whether this model is already a
    # registry entry, so the picker can show it as added instead of offering a
    # duplicate.
    in_registry: bool = False
    registry_id: Optional[str] = None


class FamilyDiscoveryPublic(BaseModel):
    family: str
    label: str
    configured: bool
    models: list[DiscoveredModelPublic] = Field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Registry import / export
# ---------------------------------------------------------------------------


class RegistryImportRequest(BaseModel):
    """A supplied model list, replacing or merging into the registry.

    Accepts either a full registry document or a bare provider array; the
    endpoint normalises both, because a file exported from this app and a list
    someone hand-wrote are both things an operator will reasonably upload.
    """

    providers: list[ProviderConfig] = Field(min_length=1)
    version: str = "1.0"
    # Merge by default: replace silently discards working models that the
    # uploaded file happens not to mention, which is a destructive default for
    # a file picker.
    mode: Literal["merge", "replace"] = "merge"

    @field_validator("providers")
    @classmethod
    def _unique_ids(cls, value: list[ProviderConfig]) -> list[ProviderConfig]:
        seen: set[str] = set()
        for provider in value:
            if provider.id in seen:
                raise ValueError(f"Duplicate provider id in upload: {provider.id}")
            seen.add(provider.id)
        return value


class RegistryImportResult(BaseModel):
    mode: str
    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    total: int = 0
    providers: list["ProviderPublic"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    """Public registration payload.

    Deliberately has no `role` field: it was previously accepted from the
    request body on an unauthenticated route, which let anyone create an admin
    account. Roles are assigned by the server and changed only by an admin
    through /auth/users/{id}/role.
    """

    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: Optional[str] = None


class RoleUpdate(BaseModel):
    role: Literal["viewer", "engineer", "admin"]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: Optional[str]
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
    audience: Optional[str] = Field(default=None, max_length=240)
    output_format: Optional[str] = Field(default=None, max_length=64)
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
    notes: Optional[str] = None


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    model_id: str
    model_name: str
    provider: str
    status: str
    content: Optional[str]
    error: Optional[str]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    overall_score: Optional[float]
    metrics: Optional[dict[str, float]]
    evaluation: Optional[dict[str, Any]]


class SectionProvenance(BaseModel):
    section: str
    source_model_id: str
    source_model_name: str
    score: float
    merged_from: list[str] = Field(default_factory=list)
    strategy: Literal[
        "adopted", "merged", "synthesized", "deduplicated", "unanimous", "reinforced"
    ] = "adopted"
    directive_count: int = 0
    unanimous_count: int = 0


class ReinforcementRecord(BaseModel):
    """A directive the engine added because no candidate supplied it."""

    id: str
    section: str
    directive: str
    rationale: str


class ConflictRecord(BaseModel):
    section: str
    kind: Literal["semantic", "syntactic", "structural"]
    description: str
    competing_models: list[str]
    resolution: str
    winner_model_id: Optional[str] = None


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
    overall_score: Optional[float]
    metrics: Optional[dict[str, float]]
    evaluation: Optional[dict[str, Any]]
    section_provenance: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    reinforcements: list[dict[str, Any]] = Field(default_factory=list)
    optimization_report: Optional[dict[str, Any]]
    token_count: int
    tokens_saved: int
    improvement_over_best: Optional[float]


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    target_domain: str
    status: str
    total_cost_usd: float
    duration_ms: Optional[int]
    created_at: datetime
    completed_at: Optional[datetime]


class RunDetail(RunSummary):
    business_problem: str
    constraints: list[str]
    requirements: list[str]
    audience: Optional[str]
    output_format: Optional[str]
    selected_model_ids: list[str]
    analysis: Optional[dict[str, Any]]
    error: Optional[str]
    total_input_tokens: int
    total_output_tokens: int
    trace_id: Optional[str]
    candidates: list[CandidateRead] = Field(default_factory=list)
    consensus: Optional[ConsensusRead] = None


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: str
    task_id: Optional[str] = None
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


class ExportFormat(str, Enum):
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

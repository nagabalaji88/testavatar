"""
Phase 0: Domain Model Contracts

All Pydantic schemas for the AI Debate & Decision Engine.
These are the contracts everything else implements against.

Core Principle: Deterministic orchestration around probabilistic models
"""

from datetime import datetime
from typing import Any, Callable, Literal, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# DEBATE SESSION
# ============================================================================

class DebateSession(BaseModel):
    """Root entity for a debate run."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID

    # Input
    question: str
    context: Optional[str] = None
    domain: Optional[str] = None
    selected_models: list[str]
    debate_strategy: Literal["standard", "deep"] = "standard"  # MVP: only standard

    # State
    status: Literal["setup", "running", "completed", "failed"] = "setup"
    current_round: int = 0
    max_rounds: int = 2  # MVP: 2 rounds max

    # Outputs
    final_decision: Optional["FinalDecisionObject"] = None

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cost_usd: float = 0.0
    tokens_total: int = 0


# ============================================================================
# MODEL ARGUMENT
# ============================================================================

class ModelArgument(BaseModel):
    """One model's position/argument in the debate."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    round: int  # 0 = independent opening, 1+ = challenge response
    model_id: str

    # Content
    position: str  # The actual argument/answer

    # Structured data
    claims: list[UUID] = Field(default_factory=list)  # Claim IDs extracted from this argument
    confidence: float  # Model's stated confidence (0-1)

    # Evolution
    previous_position: Optional[str] = None  # If this is a revision
    position_change: Optional[Literal["defend", "modify", "withdraw"]] = None
    confidence_before: Optional[float] = None
    confidence_after: Optional[float] = None

    # Metadata
    tokens_used: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# CLAIM
# ============================================================================

class Claim(BaseModel):
    """An extracted atomic claim from a model's argument."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID

    # Content
    statement: str  # The claim itself
    source_argument_id: UUID  # Which ModelArgument produced this
    source_model_id: str
    source_span: str  # EXACT text from source (for hallucination detection)

    # Classification
    claim_type: Literal["FACT", "ASSUMPTION", "INFERENCE", "OPINION", "UNVERIFIED"]
    importance: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]

    # Confidence
    confidence: float  # How confident is this claim? (0-1)

    # Hierarchy
    parent_claim_id: Optional[UUID] = None  # If this is a sub-claim
    child_claim_ids: list[UUID] = Field(default_factory=list)

    # Relationships
    supporting_claim_ids: list[UUID] = Field(default_factory=list)
    contradicting_claim_ids: list[UUID] = Field(default_factory=list)

    # Status
    status: Literal["accepted", "disputed", "unresolved"] = "unresolved"

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# CLAIM RELATIONSHIP
# ============================================================================

class ClaimRelationship(BaseModel):
    """Relationship between two claims."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID

    source_claim_id: UUID
    target_claim_id: UUID

    relationship: Literal[
        "SUPPORTS",       # Strengthens target
        "CONTRADICTS",    # Directly opposes target
        "QUALIFIES",      # Adds nuance/conditions
        "UNRELATED",      # No connection
        "UNCERTAIN"       # Context-dependent
    ]

    # Why?
    evidence: str  # Why this relationship exists
    judge_confidence: float  # How confident is this relationship? (0-1)

    # Dispute?
    disputed_by_models: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# CHALLENGE
# ============================================================================

class Challenge(BaseModel):
    """One model challenges another's claim."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID

    # Who challenges whom?
    challenger_model_id: str
    target_claim_id: UUID

    # What's the challenge?
    challenge_type: Literal[
        "incorrect_assumption",
        "logical_flaw",
        "missing_consideration",
        "unsupported_claim",
        "evidence_quality"
    ]
    challenge_text: str

    # Assessment
    severity: float  # 1-10 (how important?)
    validity_before_rebuttal: float  # 0-1 (how justified?)

    # Outcome
    validity_after_rebuttal: Optional[float] = None  # After model responds
    target_model_response: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# REBUTTAL
# ============================================================================

class Rebuttal(BaseModel):
    """One model's response to a challenge."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    challenge_id: UUID

    # Who responds?
    model_id: str

    # Response
    response_text: str

    # Position evolution
    position_before: Optional[str] = None
    position_after: Optional[str] = None
    position_change: Literal["defend", "modify", "withdraw"]

    # Confidence
    confidence_before: float
    confidence_after: float

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# JUDGE EVALUATION
# ============================================================================

class JudgeEvaluation(BaseModel):
    """Independent judge's evaluation of a position."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID

    # Judge
    judge_model_id: str
    judge_name_blinded: str  # e.g., "Position A", not "Claude"

    # What they judged
    evaluated_position: str
    evaluated_claims: list[Claim] = Field(default_factory=list)
    evaluated_challenges: list[Challenge] = Field(default_factory=list)

    # Evaluation
    overall_score: float  # 0-100
    metrics: dict[str, float]  # debate_rubric metrics
    reasoning: str

    # Alignment
    strongest_claim: str
    weakest_claim: str
    evidence_assessment: str

    # Confidence
    judge_confidence: float  # How certain is this evaluation?

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# FINAL DECISION OBJECT
# ============================================================================

class RejectedAlternative(BaseModel):
    """An alternative outcome that was considered and rejected."""

    alternative: str
    why_rejected: str
    quality_of_alternative: float  # 0-1 (was it a close call?)
    when_reconsider: str  # Condition to reconsider


class ModelPositionEvolution(BaseModel):
    """How a model's position changed during the debate."""

    model_id: str
    initial_position: str
    final_position: str
    position_changed: bool
    changes: list[str]  # ["refined X", "dropped Y", "added Z"]
    confidence_initial: float
    confidence_final: float
    influenced_by: list[str]  # Claims/challenges that influenced


class FinalDecisionObject(BaseModel):
    """The synthesized final decision and recommendation."""

    model_config = ConfigDict(from_attributes=True)

    session_id: UUID

    # Outcome
    outcome: Literal[
        "CONSENSUS",
        "MAJORITY_WITH_DISSENT",
        "SPLIT_DECISION",
        "INSUFFICIENT_EVIDENCE"
    ]

    # Recommendation
    recommendation: str

    # Confidence breakdown (4 scores, not 1 magic number)
    decision_confidence: float  # 0-1 Overall confidence
    model_agreement: float      # 0-1 What % of models agree
    evidence_strength: float    # 0-1 How strong is the evidence?
    judge_agreement: float      # 0-1 How much did judges agree?

    # Decision factors
    decision_factors: list[str] = Field(default_factory=list)

    # Claims
    supporting_claims: list[Claim] = Field(default_factory=list)
    disputed_claims: list[Claim] = Field(default_factory=list)
    unresolved_claims: list[Claim] = Field(default_factory=list)

    # Arguments
    strongest_argument: str = ""
    strongest_argument_source: str = ""
    strongest_counterargument: str = ""
    strongest_counterargument_source: str = ""

    # Contingency
    decision_changes_if: list[str] = Field(default_factory=list)
    rejected_alternatives: list[RejectedAlternative] = Field(default_factory=list)

    # Model positions
    model_positions: dict[str, ModelPositionEvolution] = Field(default_factory=dict)

    # Provenance
    supporting_model_ids: list[str] = Field(default_factory=list)
    dissenting_model_ids: list[str] = Field(default_factory=list)
    judge_ids: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# WEBSOCKET EVENT
# ============================================================================

class WebSocketEvent(BaseModel):
    """Event streamed to client during debate execution."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID = Field(default_factory=uuid4)
    session_id: UUID

    # Order matters
    sequence: int  # 1, 2, 3, ... detects missing events

    # What happened?
    event_type: Literal[
        "SESSION_STARTED",
        "MODEL_STARTED",
        "MODEL_COMPLETED",
        "MODEL_FAILED",
        "CLAIM_EXTRACTED",
        "CLAIM_ANALYZED",
        "DISPUTE_IDENTIFIED",
        "CHALLENGE_ISSUED",
        "REBUTTAL_RECEIVED",
        "POSITION_UPDATED",
        "JUDGE_STARTED",
        "JUDGE_SCORED",
        "DECISION_SYNTHESIZED",
        "DECISION_VERIFIED",
        "SESSION_COMPLETED",
        "SESSION_FAILED"
    ]

    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Event-specific payload
    payload: dict[str, Any] = Field(default_factory=dict)

    # Tracing
    trace_id: str
    correlation_id: str


# ============================================================================
# LLM SERVICE CONTRACTS
# ============================================================================

class ProviderConfig(BaseModel):
    """Configuration for one LLM provider."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    provider_type: Literal["openai", "anthropic", "google", "ollama", "custom"]
    model_id: str
    api_base: Optional[str] = None
    api_key_env: Optional[str] = None
    max_tokens: int = 2000
    temperature: float = 0.4
    supports_json_mode: bool = True

    # Pricing (per 1000 tokens)
    cost_per_1k_input: float
    cost_per_1k_output: float


class RetryConfig(BaseModel):
    """Configuration for retry and fallback behavior."""

    max_retries: int = 3
    base_delay_ms: int = 500
    max_delay_ms: int = 10000
    exponential_base: float = 2.0

    # Fallback
    fallback_models: list[str] = Field(default_factory=list)
    fallback_to_cheaper: bool = True


class LLMServiceConfig(BaseModel):
    """Configuration for the LLM service."""

    model_config = ConfigDict(from_attributes=True)

    providers: dict[str, ProviderConfig]
    default_provider: str
    retry_config: RetryConfig


class GenerateResult(BaseModel):
    """Result of a generate() call."""

    model_config = ConfigDict(from_attributes=True)

    content: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class StructuredResult(BaseModel):
    """Result of a generate_structured() call."""

    model_config = ConfigDict(from_attributes=True)

    data: Any  # Validated Pydantic instance
    raw_response: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class EvaluateResult(BaseModel):
    """Result of an evaluate() call."""

    model_config = ConfigDict(from_attributes=True)

    metrics: dict[str, float]
    reasoning: str
    score: float
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


# ============================================================================
# CLAIM EXTRACTION & ANALYSIS CONTRACTS
# ============================================================================

class ExtractedClaimData(BaseModel):
    """Raw claim data before validation."""

    statement: str
    claim_type: Literal["FACT", "ASSUMPTION", "INFERENCE", "OPINION", "UNVERIFIED"]
    start: Optional[int] = None
    end: Optional[int] = None


class ClaimExtractionSchema(BaseModel):
    """Schema for claim extraction output."""

    claims: list[ExtractedClaimData]


class RelationshipClassificationSchema(BaseModel):
    """Schema for relationship classification output."""

    relationship: Literal[
        "SUPPORTS",
        "CONTRADICTS",
        "QUALIFIES",
        "UNRELATED",
        "UNCERTAIN"
    ]
    reasoning: str
    confidence: float


class CriticalDispute(BaseModel):
    """A significant dispute that requires cross-examination."""

    claim_1: Claim
    claim_2: Claim
    significance: float  # 0-1 (how important?)
    supporting_models: list[str]
    opposing_models: list[str]


class DebateContext(BaseModel):
    """Context passed through debate execution."""

    session: DebateSession
    models: list[str]
    claims: list[Claim]
    critical_disputes: list[CriticalDispute]
    arguments: dict[str, ModelArgument]


class RoundOutcome(BaseModel):
    """Result of executing one debate round."""

    round_num: int
    challenges: list[Challenge]
    rebuttals: list[Rebuttal]
    position_updates: list[ModelArgument]
    new_disputes: list[CriticalDispute]


# ============================================================================
# JUDGE EVALUATION CONTRACTS
# ============================================================================

class DebateRubricSchema(BaseModel):
    """Schema for debate rubric evaluation output."""

    metrics: dict[str, float]
    overall_score: float  # 0-100
    reasoning: str
    strongest_claim: str
    weakest_claim: str
    confidence: float


class PositionEvaluation(BaseModel):
    """Evaluation of one anonymized position."""

    position_id: str
    overall_score: float
    metrics: dict[str, float]
    reasoning: str
    confidence: float


# ============================================================================
# COST TRACKING CONTRACTS
# ============================================================================

class CostOperation(BaseModel):
    """A single cost-bearing operation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    operation_type: str  # generate|extract|analyze|challenge|evaluate|judge
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# CONTROLLER CONTRACTS
# ============================================================================

class DebateControllerConfig(BaseModel):
    """Configuration for the debate controller."""

    max_rounds: int = 2
    max_cost_usd: float = 2.00
    max_tokens: int = 50000
    min_dispute_significance: float = 0.6
    productivity_threshold: float = 0.3


# ============================================================================
# UPDATE MODELS (for PATCH/POST)
# ============================================================================

class DebateSessionUpdate(BaseModel):
    """Updates for a debate session."""

    status: Optional[Literal["setup", "running", "completed", "failed"]] = None
    current_round: Optional[int] = None
    cost_usd: Optional[float] = None
    tokens_total: Optional[int] = None
    final_decision: Optional[FinalDecisionObject] = None


# ============================================================================
# Enable forward references
# ============================================================================

DebateSession.model_rebuild()
Claim.model_rebuild()
FinalDecisionObject.model_rebuild()

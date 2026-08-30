"""Relational domain model (SQLModel tables).

Deliberately does NOT use `from __future__ import annotations`. SQLModel's
relationship resolution walks real annotation objects at mapper-configuration
time; with postponed evaluation active, every annotation becomes a plain
string, and a collection annotation that is itself already a quoted forward
reference (`list["PromptRun"]`) gets stringified a second time into the
literal, unresolvable name `"list['PromptRun']"`. Classes referenced before
their own definition still need an explicit forward-reference string below;
everything else is a real, immediately-evaluated type.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


from sqlalchemy import JSON, Column, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlmodel import Field, Relationship, SQLModel

JSONB = JSON().with_variant(PG_JSONB, "postgresql")

# Every timestamp in the app is produced by _utcnow(), which is timezone-aware.
# SQLModel's plain `datetime` annotation maps to Postgres TIMESTAMP WITHOUT TIME
# ZONE unless told otherwise, and asyncpg refuses to insert an aware datetime
# into a naive column. Every datetime column below is explicit about this.
def _tz_column(*, index: bool = False, nullable: bool = True) -> Column:
    return Column(DateTime(timezone=True), index=index, nullable=nullable)



def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class RunStatus(str, Enum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    EVALUATING = "evaluating"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CandidateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    email: str = Field(index=True, unique=True, max_length=320)
    hashed_password: str = Field(max_length=255)
    full_name: Optional[str] = Field(default=None, max_length=200)
    role: str = Field(default="engineer", max_length=32)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())

    runs: list["PromptRun"] = Relationship(back_populates="owner")


class PromptRun(SQLModel, table=True):
    __tablename__ = "prompt_runs"
    __table_args__ = (Index("ix_prompt_runs_owner_created", "owner_id", "created_at"),)

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    owner_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id", index=True)

    title: str = Field(max_length=240)
    business_problem: str = Field(sa_column=Column(Text, nullable=False))
    target_domain: str = Field(max_length=160)
    constraints: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    requirements: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    audience: Optional[str] = Field(default=None, max_length=240)
    output_format: Optional[str] = Field(default=None, max_length=64)
    selected_model_ids: list[str] = Field(default_factory=list, sa_column=Column(JSONB))

    status: str = Field(default=RunStatus.QUEUED.value, max_length=32, index=True)
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    analysis: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))

    total_cost_usd: float = Field(default=0.0)
    total_input_tokens: int = Field(default=0)
    total_output_tokens: int = Field(default=0)
    duration_ms: Optional[int] = Field(default=None)

    trace_id: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=_tz_column(index=True)
    )
    started_at: Optional[datetime] = Field(default=None, sa_column=_tz_column())
    completed_at: Optional[datetime] = Field(default=None, sa_column=_tz_column())

    owner: Optional[User] = Relationship(back_populates="runs")
    candidates: list["PromptCandidate"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )
    consensus: Optional["ConsensusPrompt"] = Relationship(
        back_populates="run",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "uselist": False},
    )


class PromptCandidate(SQLModel, table=True):
    __tablename__ = "prompt_candidates"
    __table_args__ = (Index("ix_candidates_run_model", "run_id", "model_id"),)

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="prompt_runs.id", index=True)

    model_id: str = Field(max_length=80)
    model_name: str = Field(max_length=120)
    provider: str = Field(max_length=64)

    seed_prompt: Optional[str] = Field(default=None, sa_column=Column(Text))
    content: Optional[str] = Field(default=None, sa_column=Column(Text))
    status: str = Field(default=CandidateStatus.PENDING.value, max_length=24)
    error: Optional[str] = Field(default=None, sa_column=Column(Text))

    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    latency_ms: int = Field(default=0)
    attempts: int = Field(default=0)

    overall_score: Optional[float] = Field(default=None, index=True)
    metrics: Optional[dict[str, float]] = Field(default=None, sa_column=Column(JSONB))
    evaluation: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))

    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())

    run: PromptRun = Relationship(back_populates="candidates")


class ConsensusPrompt(SQLModel, table=True):
    __tablename__ = "consensus_prompts"

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="prompt_runs.id", unique=True, index=True)

    content: str = Field(sa_column=Column(Text, nullable=False))
    raw_merged_content: Optional[str] = Field(default=None, sa_column=Column(Text))
    overall_score: Optional[float] = Field(default=None)
    metrics: Optional[dict[str, float]] = Field(default=None, sa_column=Column(JSONB))
    evaluation: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))

    section_provenance: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB)
    )
    conflicts: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB)
    )
    reinforcements: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSONB)
    )
    optimization_report: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSONB)
    )

    token_count: int = Field(default=0)
    tokens_saved: int = Field(default=0)
    improvement_over_best: Optional[float] = Field(default=None)
    vector_point_id: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())

    run: PromptRun = Relationship(back_populates="consensus")


class ExecutionLog(SQLModel, table=True):
    """Append-only telemetry of every LLM invocation in a run."""

    __tablename__ = "execution_logs"
    __table_args__ = (Index("ix_execution_logs_run_stage", "run_id", "stage"),)

    id: uuid.UUID = Field(default_factory=_uuid, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="prompt_runs.id", index=True)

    stage: str = Field(max_length=48)
    model_id: Optional[str] = Field(default=None, max_length=80)
    status: str = Field(max_length=24)
    latency_ms: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    attempts: int = Field(default=1)
    span_id: Optional[str] = Field(default=None, max_length=64)
    detail: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSONB))
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=_tz_column(index=True)
    )


class ProviderCredential(SQLModel, table=True):
    """A provider API key entered through the admin UI.

    Keyed by provider *family* ("openai", "anthropic", ...) rather than by
    registry entry, because that is the granularity a key actually has: one
    OPENAI_API_KEY authenticates every OpenAI model in the registry. A single
    entry needing its own key still uses ProviderConfig.api_key_env.

    The value is stored encrypted (app.core.crypto) and is never read back out
    over the API -- only `last4`, which is enough to confirm *which* key is in
    place without disclosing it.
    """

    __tablename__ = "provider_credentials"

    family: str = Field(primary_key=True, max_length=64)
    encrypted_key: str = Field(sa_column=Column(Text, nullable=False))
    # Denormalised so the UI can show which key is stored without decrypting
    # on every list request -- and so it still shows something useful when the
    # encryption key has rotated and the ciphertext is no longer readable.
    last4: str = Field(default="", max_length=8)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())
    # Audit only, and deliberately NOT a foreign key. It records who last
    # touched the key; it must never be the reason saving one fails. A
    # constraint here would turn a deleted or unrecognised admin id into an
    # integrity error on the one operation an operator performs to *recover*
    # access to their providers.
    updated_by: Optional[uuid.UUID] = Field(default=None)

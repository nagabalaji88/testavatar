"""Application configuration loaded from the environment.

All settings are typed and validated at import time so a misconfigured
deployment fails fast at boot rather than at first request.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Service identity -------------------------------------------------
    app_name: str = "Master Prompt Generator"
    environment: Literal["local", "staging", "production"] = "local"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    # --- Persistence ------------------------------------------------------
    # Defaults are the no-service path: a single SQLite file, no Redis, no
    # Celery. Point these at PostgreSQL and Redis to scale out; nothing else
    # in the app changes.
    database_url: str = "sqlite+aiosqlite:///./data/mpg.db"
    sqlite_directory: str = "./data"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Empty means "run in-process": the event bus, rate limiter and revocation
    # store all fall back to in-memory implementations.
    redis_url: str = ""
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    # Empty disables the vector index entirely; semantic search then returns
    # nothing and the pipeline is unaffected.
    qdrant_url: str = ""
    qdrant_api_key: Optional[str] = None
    qdrant_collection: str = "mpg_prompts"

    # Embeddings. "ollama" keeps the whole stack open-source and key-free;
    # switching provider changes the vector width, so the collection must be
    # recreated when this changes.
    embedding_provider: Literal["openai", "ollama", "disabled"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # --- Security ---------------------------------------------------------
    DEFAULT_JWT_SECRET: ClassVar[str] = "change-me-in-production"

    jwt_secret_key: str = Field(default=DEFAULT_JWT_SECRET, min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_minutes: int = 60 * 24 * 14
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Open registration is convenient locally and a liability in production.
    # The very first account is always promoted to admin so the instance is
    # usable; every later account is created as an engineer.
    allow_open_registration: bool = True

    # Reject a revoked token when the revocation store is unreachable. Defaults
    # on in production (correctness over availability) and off locally.
    strict_token_revocation: Optional[bool] = None

    # --- Rate limits (per identity, sliding fixed window) ------------------
    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_hour: int = 5
    rate_limit_runs_per_hour: int = 30

    # --- Outbound endpoint policy ----------------------------------------
    # A provider's api_base is attacker-reachable via the admin model registry,
    # so pointing it at a private address is an SSRF primitive. Local inference
    # legitimately needs private hosts, so they are allowed only by name.
    api_base_allowlist: list[str] = [
        "localhost",
        "127.0.0.1",
        "::1",
        "ollama",
        "vllm",
        "host.docker.internal",
    ]

    # --- Provider credentials --------------------------------------------
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None

    # Hosted gateways that serve open-weight models. Both are optional; the
    # Ollama path below needs no credential at all.
    groq_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    together_api_key: Optional[str] = None
    huggingface_api_key: Optional[str] = None

    # --- Local open-source inference --------------------------------------
    # Providers whose `provider` field names a local runtime inherit this base
    # URL unless the registry entry overrides it, so the same models.json works
    # inside Compose (http://ollama:11434) and on a workstation (localhost).
    ollama_base_url: str = "http://ollama:11434"
    vllm_base_url: Optional[str] = None

    # --- Orchestration tuning --------------------------------------------
    model_config_path: Path = BACKEND_ROOT / "config" / "models.json"
    generation_timeout_seconds: int = 180
    judge_timeout_seconds: int = 120
    max_parallel_generations: int = 8
    llm_max_retries: int = 3
    llm_retry_base_delay: float = 1.5
    judge_model_id: str = "anthropic-claude-sonnet"
    consensus_model_id: str = "anthropic-claude-sonnet"
    analyzer_model_id: str = "openai-gpt4o"

    # --- Observability ----------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: Optional[str] = None
    otel_service_name: str = "mpg-backend"
    log_level: str = "INFO"
    metrics_path: str = "/metrics"

    @field_validator("cors_origins", "api_base_allowlist", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _enforce_deployment_safety(self) -> "Settings":
        """Refuse to boot a non-local deployment with give-away defaults.

        Every one of these is exploitable the moment the service is reachable,
        and each has been shipped by someone who assumed the default was a
        placeholder rather than a live value.
        """
        if self.environment == "local":
            if self.strict_token_revocation is None:
                self.strict_token_revocation = False
            return self

        problems: list[str] = []
        if self.jwt_secret_key == self.DEFAULT_JWT_SECRET:
            problems.append(
                "JWT_SECRET_KEY is still the shipped default, so anyone can "
                "forge an admin token. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        elif len(self.jwt_secret_key) < 32:
            problems.append("JWT_SECRET_KEY must be at least 32 characters.")

        if "*" in self.cors_origins:
            problems.append(
                "CORS_ORIGINS may not be '*' while credentials are allowed."
            )
        if ":mpg@" in self.database_url or "://mpg:mpg" in self.database_url:
            problems.append("DATABASE_URL still uses the default mpg/mpg credentials.")

        if problems:
            raise ValueError(
                f"Refusing to start in environment={self.environment}:\n  - "
                + "\n  - ".join(problems)
            )

        if self.strict_token_revocation is None:
            self.strict_token_revocation = True
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def use_redis(self) -> bool:
        """False when running single-process with in-memory infrastructure."""
        return bool(self.redis_url.strip())

    @property
    def use_celery(self) -> bool:
        """False when the pipeline should execute inline in the API process."""
        return bool(self.celery_broker_url.strip())

    @property
    def frontend_dist(self) -> Path:
        """Built SPA served directly by FastAPI, so no web server is required."""
        return BACKEND_ROOT.parent / "frontend" / "dist"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()

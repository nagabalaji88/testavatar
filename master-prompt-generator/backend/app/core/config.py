"""Application configuration loaded from the environment.

All settings are typed and validated at import time so a misconfigured
deployment fails fast at boot rather than at first request.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
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
    database_url: str = "postgresql+asyncpg://mpg:mpg@postgres:5432/mpg"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: Optional[str] = None
    qdrant_collection: str = "mpg_prompts"

    # Embeddings. "ollama" keeps the whole stack open-source and key-free;
    # switching provider changes the vector width, so the collection must be
    # recreated when this changes.
    embedding_provider: Literal["openai", "ollama", "disabled"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # --- Security ---------------------------------------------------------
    jwt_secret_key: str = Field(default="change-me-in-production", min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60
    refresh_token_ttl_minutes: int = 60 * 24 * 14
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()

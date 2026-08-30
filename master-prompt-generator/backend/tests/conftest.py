"""Isolate the suite from the deployment it happens to be running inside.

Several tests assert on *defaults* -- that the production guard rejects the
built-in SQLite URL, that a provider with no key is reported as needing one,
that a local runtime falls back to the configured Ollama host. Compose passes
the real deployment's environment into the test container, so those variables
were answering instead of the defaults, and the assertions failed for reasons
that had nothing to do with the code under test:

  * DATABASE_URL was the compose Postgres URL, so the SQLite-default guard
    never fired and a different guard's message came back;
  * OPENAI_API_KEY held the operator's real key, so "a provider with no key"
    silently had one;
  * OLLAMA_BASE_URL pointed at host.docker.internal rather than the default.

Clearing them at import time rather than in a fixture is deliberate: pytest
imports this module before any test module, and `settings` is a singleton built
on first import of app.core.config. A fixture would run too late to affect it.

Provider credentials are also cleared from the credential *store* per test, so
one test storing a key cannot make another test's "no key configured" case
quietly false.
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest

# Variables that describe the surrounding deployment. Every one of these has a
# default in Settings that some test asserts against.
_DEPLOYMENT_VARS = (
    "DATABASE_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "QDRANT_URL",
    "MODEL_CONFIG_PATH",
    "OLLAMA_BASE_URL",
    "VLLM_BASE_URL",
    "CORS_ORIGINS",
    "ENVIRONMENT",
    "ALLOW_OPEN_REGISTRATION",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "ANALYZER_MODEL_ID",
    "JUDGE_MODEL_ID",
    "CONSENSUS_MODEL_ID",
)

# Provider keys. A test that says "no key is configured" has to be true.
_CREDENTIAL_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "HUGGINGFACE_API_KEY",
    "OLLAMA_CLOUD_API_KEY",
    "CREDENTIAL_ENCRYPTION_KEY",
)

for _name in (*_DEPLOYMENT_VARS, *_CREDENTIAL_VARS):
    os.environ.pop(_name, None)

# The suite needs *a* signing secret, and the guard rejects short ones. Set
# after the purge so it survives it.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-" + "x" * 40)


@pytest.fixture(autouse=True)
def _clean_credential_state() -> Iterator[None]:
    """Reset the process-wide credential caches around every test.

    Both are module-level singletons: a stored key or a memoised cipher would
    otherwise outlive the test that created it and change what the next one
    observes.
    """
    from app.core.crypto import reset_cipher_cache
    from app.services.credential_store import credential_store

    credential_store._keys.clear()
    credential_store._undecryptable.clear()
    credential_store._loaded_at = 0.0
    reset_cipher_cache()

    yield

    credential_store._keys.clear()
    credential_store._undecryptable.clear()
    credential_store._loaded_at = 0.0
    reset_cipher_cache()

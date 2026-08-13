"""Security regression tests.

Each case here corresponds to a defect found in a security review of this
codebase. They exist to make sure the hole cannot silently reopen.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.net import UnsafeEndpointError, validate_api_base
from app.core.security import Role, create_access_token, decode_token
from app.models.schemas import ProviderConfig, RoleUpdate, UserCreate


def _provider(api_base: str) -> ProviderConfig:
    return ProviderConfig(
        id="p",
        name="P",
        provider="Ollama",
        model_key="m",
        max_tokens=1024,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        api_base=api_base,
    )


class TestRegistrationPrivilegeEscalation:
    """Public registration accepted a `role`, so anyone could create an admin."""

    def test_role_cannot_be_supplied_at_registration(self) -> None:
        assert "role" not in UserCreate.model_fields

    def test_role_in_the_body_is_ignored_not_honoured(self) -> None:
        payload = UserCreate.model_validate(
            {
                "email": "attacker@example.com",
                "password": "correct-horse-battery",
                "role": "admin",
            }
        )
        assert not hasattr(payload, "role")

    def test_role_changes_still_have_a_typed_admin_only_path(self) -> None:
        assert RoleUpdate(role="admin").role == "admin"
        with pytest.raises(ValidationError):
            RoleUpdate(role="superuser")


class TestApiBaseSsrf:
    """api_base is admin-writable and fetched server-side."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5:8000",
            "http://192.168.1.1",
            "http://172.16.0.9:11434",
            "file:///etc/passwd",
            "gopher://internal:70/",
            "http://user:pass@example.com",
            "http://",
        ],
    )
    def test_unsafe_endpoints_are_rejected(self, url: str) -> None:
        with pytest.raises((UnsafeEndpointError, ValueError)):
            validate_api_base(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://ollama:11434",
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            # IPv6 loopback is allowlisted alongside 127.0.0.1 for local inference.
            "http://[::1]:11434",
            "https://api.groq.com/openai/v1",
        ],
    )
    def test_legitimate_endpoints_are_allowed(self, url: str) -> None:
        assert validate_api_base(url) == url

    def test_the_schema_enforces_it_not_just_the_helper(self) -> None:
        with pytest.raises(ValidationError):
            _provider("http://169.254.169.254/")
        assert _provider("http://ollama:11434").api_base == "http://ollama:11434"


class TestDeploymentGuards:
    """A non-local deployment must not boot on shipped defaults."""

    def test_local_still_boots_with_defaults(self) -> None:
        assert Settings(environment="local").jwt_secret_key

    def test_production_refuses_the_default_secret(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Settings(environment="production")
        assert "JWT_SECRET_KEY" in str(exc.value)

    def test_production_refuses_a_short_secret(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Settings(environment="production", jwt_secret_key="tooshort123")
        assert "32 characters" in str(exc.value)

    def test_production_refuses_wildcard_cors(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Settings(
                environment="production",
                jwt_secret_key="x" * 48,
                cors_origins=["*"],
                database_url="postgresql+asyncpg://u:strongpw@db:5432/mpg",
            )
        assert "CORS_ORIGINS" in str(exc.value)

    def test_production_refuses_default_database_credentials(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Settings(environment="production", jwt_secret_key="x" * 48)
        assert "DATABASE_URL" in str(exc.value)

    def test_a_correctly_configured_production_boots(self) -> None:
        settings = Settings(
            environment="production",
            jwt_secret_key="x" * 48,
            cors_origins=["https://mpg.example.com"],
            database_url="postgresql+asyncpg://mpg:s3cret-pw@db:5432/mpg",
        )
        assert settings.is_production
        assert settings.strict_token_revocation is True

    def test_local_defaults_to_lenient_revocation(self) -> None:
        assert Settings(environment="local").strict_token_revocation is False


class TestTokenClaims:
    def test_tokens_carry_a_jti_and_iat_so_they_can_be_revoked(self) -> None:
        token = create_access_token("11111111-1111-1111-1111-111111111111", Role.ENGINEER)
        claims = decode_token(token)
        assert claims.jti and claims.iat and claims.exp > claims.iat

    def test_each_token_gets_a_unique_jti(self) -> None:
        subject = "11111111-1111-1111-1111-111111111111"
        first = decode_token(create_access_token(subject, Role.ENGINEER))
        second = decode_token(create_access_token(subject, Role.ENGINEER))
        assert first.jti != second.jti

    def test_a_refresh_token_is_not_accepted_as_an_access_token(self) -> None:
        from app.core.security import create_refresh_token
        from fastapi import HTTPException

        token = create_refresh_token("11111111-1111-1111-1111-111111111111", Role.ADMIN)
        with pytest.raises(HTTPException):
            decode_token(token, expected_type="access")

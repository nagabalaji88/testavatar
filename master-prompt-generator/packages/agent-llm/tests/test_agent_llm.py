"""Behaviour of the extracted client, without touching a provider."""

from __future__ import annotations

import os
from types import SimpleNamespace

import httpx
import pytest
from litellm.exceptions import AuthenticationError, RateLimitError

from agent_llm import (
    ClientOptions,
    LLMClient,
    LLMError,
    ModelSpec,
    RetryPolicy,
    UnsafeEndpointError,
    extract_json_object,
    from_env,
    validate_endpoint,
)

SPEC = ModelSpec(
    key="anthropic/claude-sonnet-5",
    name="Sonnet",
    api_key_env="TEST_AGENT_LLM_KEY",
    cost_per_1k_input=0.003,
    cost_per_1k_output=0.015,
)


def _client(**opts) -> LLMClient:
    opts.setdefault("pin_connections", False)
    return LLMClient(SPEC, ClientOptions(**opts))


def _response(content: str, prompt: int = 100, completion: int = 50, finish="stop"):
    return SimpleNamespace(
        id="resp-1",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason=finish
            )
        ],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


class TestCredentials:
    def test_the_key_is_read_at_call_time_not_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rotated key must be picked up without rebuilding the client."""
        monkeypatch.delenv("TEST_AGENT_LLM_KEY", raising=False)
        client = _client()
        assert client.api_key() is None
        monkeypatch.setenv("TEST_AGENT_LLM_KEY", "sk-rotated")
        assert client.api_key() == "sk-rotated"

    def test_readiness_reports_a_missing_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TEST_AGENT_LLM_KEY", raising=False)
        assert _client().is_ready() is False
        monkeypatch.setenv("TEST_AGENT_LLM_KEY", "sk-present")
        assert _client().is_ready() is True

    def test_a_blank_variable_is_missing_not_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_AGENT_LLM_KEY", "   ")
        assert _client().api_key() is None

    def test_a_model_needing_no_key_is_always_ready(self) -> None:
        local = LLMClient(
            ModelSpec(key="ollama_chat/qwen2.5:7b-instruct"),
            ClientOptions(pin_connections=False),
        )
        assert local.is_ready() is True


class TestRetryClassification:
    @pytest.mark.asyncio
    async def test_a_transient_failure_is_retried_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"n": 0}

        async def flaky(**_kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RateLimitError("slow down", llm_provider="anthropic", model="m")
            return _response("done")

        monkeypatch.setattr("litellm.acompletion", flaky)
        client = _client(retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.0))
        result = await client.complete(system="s", user="u")
        assert result.content == "done"
        assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_a_bad_credential_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retrying a 401 burns the ladder and fails anyway."""
        calls = {"n": 0}

        async def unauthorized(**_kwargs):
            calls["n"] += 1
            raise AuthenticationError(
                "bad key", llm_provider="anthropic", model="m"
            )

        monkeypatch.setattr("litellm.acompletion", unauthorized)
        client = _client(retry=RetryPolicy(max_attempts=5, base_delay_seconds=0.0))
        with pytest.raises(LLMError) as exc:
            await client.complete(system="s", user="u")
        assert exc.value.retryable is False
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_exhausting_the_ladder_raises_retryable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def always_limited(**_kwargs):
            raise RateLimitError("nope", llm_provider="anthropic", model="m")

        monkeypatch.setattr("litellm.acompletion", always_limited)
        client = _client(retry=RetryPolicy(max_attempts=2, base_delay_seconds=0.0))
        with pytest.raises(LLMError) as exc:
            await client.complete(system="s", user="u")
        assert exc.value.retryable is True


class TestAccounting:
    @pytest.mark.asyncio
    async def test_cost_follows_the_spec_prices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def ok(**_kwargs):
            return _response("hi", prompt=1000, completion=1000)

        monkeypatch.setattr("litellm.acompletion", ok)
        result = await _client().complete(system="s", user="u")
        assert result.cost_usd == pytest.approx(0.003 + 0.015)
        assert result.total_tokens == 2000

    @pytest.mark.asyncio
    async def test_a_provider_without_usage_still_reports_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Self-hosted runtimes often omit usage; zero would break budgets."""

        async def no_usage(**_kwargs):
            return SimpleNamespace(
                id="r",
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="a b c d e"),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

        monkeypatch.setattr("litellm.acompletion", no_usage)
        result = await _client().complete(system="s", user="u")
        assert result.output_tokens > 0

    @pytest.mark.asyncio
    async def test_truncation_is_visible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A truncated reply is a successful call with an unusable result."""

        async def cut_off(**_kwargs):
            return _response("half a sen", finish="length")

        monkeypatch.setattr("litellm.acompletion", cut_off)
        assert (await _client().complete(system="s", user="u")).truncated is True

    @pytest.mark.asyncio
    async def test_the_result_hook_fires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def ok(**_kwargs):
            return _response("hi")

        monkeypatch.setattr("litellm.acompletion", ok)
        seen: list = []
        await _client(on_result=seen.append).complete(system="s", user="u")
        assert len(seen) == 1


class TestJson:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"a": 1}',
            'Sure! Here you go:\n```json\n{"a": 1}\n```',
            'Prose first. {"a": 1} Trailing commentary.',
            '```\n{"a": 1}\n```',
        ],
    )
    def test_json_survives_the_wrapping_models_add(self, raw: str) -> None:
        assert extract_json_object(raw) == {"a": 1}

    def test_a_brace_inside_a_string_does_not_end_the_scan(self) -> None:
        assert extract_json_object('{"a": "} not the end"}') == {"a": "} not the end"}

    def test_unparseable_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json_object("no object here")

    @pytest.mark.asyncio
    async def test_one_repair_call_is_issued_and_billed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both attempts must be charged, or accounting under-reports."""
        replies = ["I cannot do that", '{"ok": true}']

        async def two_step(**_kwargs):
            return _response(replies.pop(0), prompt=100, completion=100)

        monkeypatch.setattr("litellm.acompletion", two_step)
        payload, result = await _client().complete_json(system="s", user="u")
        assert payload == {"ok": True}
        assert result.input_tokens == 200
        assert result.cost_usd == pytest.approx(2 * (0.0003 + 0.0015))


class TestEndpointSafety:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5:8000",
            "file:///etc/passwd",
            "http://user:pass@example.com",
        ],
    )
    def test_unsafe_endpoints_are_refused(self, url: str) -> None:
        with pytest.raises(UnsafeEndpointError):
            validate_endpoint(url, resolve=False)

    def test_allowlisted_local_runtimes_are_permitted(self) -> None:
        for url in ("http://localhost:11434", "http://ollama:11434"):
            assert validate_endpoint(url, resolve=False) == url

    def test_a_name_resolving_inward_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("agent_llm.net._resolve", lambda _h: ["127.0.0.1"])
        with pytest.raises(UnsafeEndpointError):
            validate_endpoint("http://attacker.example.com/v1")

    def test_a_bad_endpoint_fails_at_construction(self) -> None:
        """Configuration errors belong at startup, not mid-task."""
        with pytest.raises(UnsafeEndpointError):
            LLMClient(
                ModelSpec(key="openai/x", api_base="http://169.254.169.254/v1"),
                ClientOptions(pin_connections=False),
            )

    @pytest.mark.asyncio
    async def test_the_transport_pins_and_preserves_the_hostname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_llm.net import PinnedResolutionTransport

        captured: dict = {}

        async def capture(self, request):  # noqa: ANN001
            captured["url"] = request.url
            captured["host"] = request.headers.get("Host")
            captured["sni"] = request.extensions.get("sni_hostname")
            raise RuntimeError("stop here")

        monkeypatch.setattr("agent_llm.net._resolve", lambda _h: ["93.184.216.34"])
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", capture)

        with pytest.raises(RuntimeError):
            await PinnedResolutionTransport().handle_async_request(
                httpx.Request("GET", "https://api.example.com/v1/chat")
            )

        assert captured["url"].host == "93.184.216.34"
        # Both are required or TLS verification fails against the bare IP.
        assert captured["host"] == "api.example.com"
        assert captured["sni"] == "api.example.com"


class TestFromEnv:
    def test_a_missing_model_says_what_to_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)
        with pytest.raises(ValueError, match="AGENT_LLM_MODEL"):
            from_env()

    def test_it_builds_a_configured_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_LLM_MODEL", "openai/gpt-4o")
        monkeypatch.setenv("AGENT_LLM_API_KEY_ENV", "OPENAI_API_KEY")
        monkeypatch.setenv("AGENT_LLM_MAX_TOKENS", "8192")
        monkeypatch.setenv("AGENT_LLM_COST_IN", "0.0025")
        client = from_env()
        assert client.model.key == "openai/gpt-4o"
        assert client.model.max_tokens == 8192
        assert client.model.cost_per_1k_input == 0.0025

    def test_the_spec_never_holds_the_secret_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole configuration must be safe to log or commit."""
        monkeypatch.setenv("AGENT_LLM_MODEL", "openai/gpt-4o")
        monkeypatch.setenv("AGENT_LLM_API_KEY_ENV", "OPENAI_API_KEY")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-appear")
        assert "sk-must-not-appear" not in repr(from_env().model)


class TestSpecValidation:
    def test_an_empty_key_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ModelSpec(key="")

    def test_a_nonpositive_ceiling_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ModelSpec(key="openai/gpt-4o", max_tokens=0)

    def test_a_self_hosted_model_costs_nothing(self) -> None:
        assert ModelSpec(key="ollama_chat/x").estimate_cost(10_000, 10_000) == 0.0

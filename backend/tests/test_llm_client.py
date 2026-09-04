"""Tests for the real Anthropic/OpenAI failover LLM client.

No live network calls — the SDK client methods are monkeypatched so these
run in CI / without API keys, but exercise the actual failover logic.
"""

from __future__ import annotations

import asyncio

import pytest

import anthropic
import openai

from backend.platform.llm_client import LLMClient, get_llm_client


def _clear_settings_cache():
    import backend.platform.config as config_module

    config_module._settings = None


@pytest.fixture(autouse=True)
def reset_settings():
    _clear_settings_cache()
    yield
    _clear_settings_cache()


def _conn_error(msg="boom"):
    return anthropic.APIConnectionError(message=msg, request=None)


def _openai_conn_error(msg="boom"):
    import httpx

    return openai.APIConnectionError(message=msg, request=httpx.Request("POST", "http://x"))


class _RecordingAnthropicMessages:
    """Records the kwargs of every call; can raise N times before succeeding."""

    def __init__(self, response_text: str | None = "ok", fail_times: int = 0, exc_factory=None):
        self.calls: list[dict] = []
        self._response_text = response_text
        self._fail_times = fail_times
        self._exc_factory = exc_factory or _conn_error

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_times > 0:
            self._fail_times -= 1
            raise self._exc_factory()
        block = type("Block", (), {"type": "text", "text": self._response_text})()
        return type("Message", (), {"content": [block]})()


class _RecordingOpenAIChoice:
    def __init__(self, content):
        self.message = type("Msg", (), {"content": content})()


class _RecordingOpenAICompletions:
    def __init__(self, response_text: str | None = "ok", fail_times: int = 0, exc_factory=None):
        self.calls: list[dict] = []
        self._response_text = response_text
        self._fail_times = fail_times
        self._exc_factory = exc_factory or _openai_conn_error

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_times > 0:
            self._fail_times -= 1
            raise self._exc_factory()
        return type("Completion", (), {"choices": [_RecordingOpenAIChoice(self._response_text)]})()


def _wire_anthropic(client, **kwargs):
    fake = _RecordingAnthropicMessages(**kwargs)
    client._anthropic_client.messages = fake
    return fake


def _wire_openai(client, **kwargs):
    fake = _RecordingOpenAICompletions(**kwargs)
    client._openai_client.chat = type("Chat", (), {"completions": fake})()
    return fake


def _both_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    _clear_settings_cache()


class TestNoApiKeysConfigured:
    @pytest.mark.asyncio
    async def test_no_keys_returns_mock_response(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        client = LLMClient(provider="anthropic")
        result = await client.generate("What happened?", system="You are an SRE.")
        assert "confidence" in result.lower()

    @pytest.mark.asyncio
    async def test_no_keys_not_marked_as_fallback(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        client = LLMClient(provider="anthropic")
        await client.generate("test")
        assert client.was_fallback_used is False

    @pytest.mark.asyncio
    async def test_only_primary_missing_key_still_returns_mock_before_trying_secondary(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        client = LLMClient(provider="openai")
        result = await client.generate("test")
        assert isinstance(result, str) and len(result) > 0

    @pytest.mark.asyncio
    async def test_generate_with_empty_prompt_does_not_crash(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        client = LLMClient()
        result = await client.generate("")
        assert isinstance(result, str)


class TestFailover:
    @pytest.mark.asyncio
    async def test_primary_failure_falls_over_to_secondary(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        _wire_anthropic(client, fail_times=99)
        _wire_openai(client, response_text="Root cause: bad deploy.")

        result = await client.generate("Analyze logs")
        assert result == "Root cause: bad deploy."
        assert client.was_fallback_used is True

    @pytest.mark.asyncio
    async def test_both_providers_fail_falls_back_to_mock(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        _wire_anthropic(client, fail_times=99, exc_factory=lambda: RuntimeError("anthropic down"))
        _wire_openai(client, fail_times=99, exc_factory=lambda: RuntimeError("openai down"))

        result = await client.generate("Analyze logs")
        assert "confidence" in result.lower()

    @pytest.mark.asyncio
    async def test_primary_success_never_touches_secondary(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        _wire_anthropic(client, response_text="All good.")

        def _boom(**kwargs):
            raise AssertionError("secondary provider should never be called")

        client._openai_client.chat = type("Chat", (), {"completions": type("C", (), {"create": _boom})()})()

        result = await client.generate("Analyze logs")
        assert result == "All good."
        assert client.was_fallback_used is False

    @pytest.mark.asyncio
    async def test_openai_primary_falls_over_to_anthropic(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="openai")
        _wire_openai(client, fail_times=99)
        _wire_anthropic(client, response_text="Anthropic saved the day.")

        result = await client.generate("Analyze")
        assert result == "Anthropic saved the day."
        assert client.was_fallback_used is True

    @pytest.mark.asyncio
    async def test_same_provider_retry_succeeds_without_failover(self, monkeypatch):
        """One transient connection error, then success — must NOT fail over
        to the secondary provider at all."""
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        fake = _wire_anthropic(client, fail_times=1, response_text="recovered")

        def _boom(**kwargs):
            raise AssertionError("should not fail over — same-provider retry should have succeeded")

        client._openai_client.chat = type("Chat", (), {"completions": type("C", (), {"create": _boom})()})()

        result = await client.generate("Analyze")
        assert result == "recovered"
        assert len(fake.calls) == 2  # first failed, retry succeeded
        assert client.was_fallback_used is False

    @pytest.mark.asyncio
    async def test_same_provider_retry_exhausted_then_fails_over(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        _wire_anthropic(client, fail_times=99)  # keeps failing even after the one retry
        _wire_openai(client, response_text="secondary handled it")

        result = await client.generate("Analyze")
        assert result == "secondary handled it"
        assert client.was_fallback_used is True

    @pytest.mark.asyncio
    async def test_timeout_error_triggers_failover(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        _wire_anthropic(client, fail_times=99, exc_factory=lambda: anthropic.APITimeoutError(request=None))
        _wire_openai(client, response_text="handled timeout")

        result = await client.generate("Analyze")
        assert result == "handled timeout"

    @pytest.mark.asyncio
    async def test_rate_limit_error_triggers_failover(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")

        def _rate_limited():
            import httpx

            resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
            return anthropic.RateLimitError("rate limited", response=resp, body=None)

        _wire_anthropic(client, fail_times=99, exc_factory=_rate_limited)
        _wire_openai(client, response_text="handled rate limit")

        result = await client.generate("Analyze")
        assert result == "handled rate limit"

    @pytest.mark.asyncio
    async def test_fallback_flag_resets_on_next_successful_primary_call(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        fake = _wire_anthropic(client, fail_times=99)
        _wire_openai(client, response_text="fallback response")
        await client.generate("first call")
        assert client.was_fallback_used is True

        # Now make the primary healthy again for the second call.
        fake._fail_times = 0
        fake._response_text = "primary healthy again"
        result = await client.generate("second call")
        assert result == "primary healthy again"
        assert client.was_fallback_used is False


class TestRequestParameters:
    @pytest.mark.asyncio
    async def test_anthropic_receives_correct_kwargs(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        fake = _wire_anthropic(client)
        await client.generate("my prompt", system="my system", max_tokens=42, temperature=0.9)

        call = fake.calls[0]
        assert call["max_tokens"] == 42
        assert call["temperature"] == 0.9
        assert call["system"] == "my system"
        assert call["messages"] == [{"role": "user", "content": "my prompt"}]

    @pytest.mark.asyncio
    async def test_anthropic_empty_system_uses_not_given(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        fake = _wire_anthropic(client)
        await client.generate("prompt only")
        assert fake.calls[0]["system"] is anthropic.NOT_GIVEN

    @pytest.mark.asyncio
    async def test_openai_receives_correct_kwargs_with_system(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="openai")
        fake = _wire_openai(client)
        await client.generate("my prompt", system="be helpful", max_tokens=7, temperature=0.1)

        call = fake.calls[0]
        assert call["max_tokens"] == 7
        assert call["temperature"] == 0.1
        assert call["messages"] == [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "my prompt"},
        ]

    @pytest.mark.asyncio
    async def test_openai_without_system_omits_system_message(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="openai")
        fake = _wire_openai(client)
        await client.generate("just a prompt")
        assert fake.calls[0]["messages"] == [{"role": "user", "content": "just a prompt"}]

    @pytest.mark.asyncio
    async def test_default_max_tokens_and_temperature(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        fake = _wire_anthropic(client)
        await client.generate("prompt")
        assert fake.calls[0]["max_tokens"] == 1024
        assert fake.calls[0]["temperature"] == 0.0


class TestResponseParsing:
    @pytest.mark.asyncio
    async def test_anthropic_joins_multiple_text_blocks(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        blocks = [
            type("Block", (), {"type": "text", "text": "Part one. "})(),
            type("Block", (), {"type": "text", "text": "Part two."})(),
        ]

        async def _create(**kwargs):
            return type("Message", (), {"content": blocks})()

        client._anthropic_client.messages = type("M", (), {"create": staticmethod(_create)})()
        result = await client.generate("prompt")
        assert result == "Part one. Part two."

    @pytest.mark.asyncio
    async def test_anthropic_ignores_non_text_blocks(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")
        blocks = [
            type("Block", (), {"type": "tool_use", "text": None})(),
            type("Block", (), {"type": "text", "text": "final answer"})(),
        ]

        async def _create(**kwargs):
            return type("Message", (), {"content": blocks})()

        client._anthropic_client.messages = type("M", (), {"create": staticmethod(_create)})()
        result = await client.generate("prompt")
        assert result == "final answer"

    @pytest.mark.asyncio
    async def test_anthropic_empty_content_returns_empty_string(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")

        async def _create(**kwargs):
            return type("Message", (), {"content": []})()

        client._anthropic_client.messages = type("M", (), {"create": staticmethod(_create)})()
        result = await client.generate("prompt")
        assert result == ""

    @pytest.mark.asyncio
    async def test_openai_none_content_returns_empty_string(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="openai")
        _wire_openai(client, response_text=None)
        result = await client.generate("prompt")
        assert result == ""


class TestProviderSelection:
    def test_default_provider_from_settings(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        _clear_settings_cache()
        client = LLMClient()
        assert client.primary_provider == "anthropic"
        assert client.secondary_provider == "openai"

    def test_openai_primary_flips_secondary_to_anthropic(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        _clear_settings_cache()
        client = LLMClient()
        assert client.primary_provider == "openai"
        assert client.secondary_provider == "anthropic"

    def test_explicit_provider_overrides_settings(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        _clear_settings_cache()
        client = LLMClient(provider="openai")
        assert client.primary_provider == "openai"

    @pytest.mark.asyncio
    async def test_unknown_provider_degrades_to_mock_not_crash(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="carrier-pigeon")
        client.secondary_provider = "anthropic"
        _wire_anthropic(client, response_text="ok")
        result = await client.generate("test")
        assert isinstance(result, str)


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_calls_do_not_interfere(self, monkeypatch):
        _both_keys(monkeypatch)
        client = LLMClient(provider="anthropic")

        counter = {"n": 0}

        async def _create(**kwargs):
            counter["n"] += 1
            await asyncio.sleep(0.01)
            block = type("Block", (), {"type": "text", "text": f"response-{kwargs['messages'][0]['content']}"})()
            return type("Message", (), {"content": [block]})()

        client._anthropic_client.messages = type("M", (), {"create": staticmethod(_create)})()

        results = await asyncio.gather(*[client.generate(f"prompt-{i}") for i in range(10)])
        assert counter["n"] == 10
        assert len(set(results)) == 10  # each call got its own distinct prompt echoed back


class TestSingleton:
    def test_get_llm_client_returns_same_instance(self):
        import backend.platform.llm_client as llm_module

        llm_module._llm_client = None
        a = get_llm_client()
        b = get_llm_client()
        assert a is b
        llm_module._llm_client = None

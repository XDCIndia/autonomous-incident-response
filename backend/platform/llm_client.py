"""LLM client abstraction — provider-independent interface with failover.

Real Anthropic/OpenAI integration (Person 5 / Platform-Integration).

Supports:
- Anthropic (Claude)
- OpenAI (GPT-4)
- Automatic failover between providers on timeout/connection/rate-limit errors
- Deterministic mock fallback when no API key is configured, so the demo
  never hard-fails just because a key wasn't set.
"""

from __future__ import annotations

import asyncio
import logging

import anthropic
import openai

from backend.platform.config import get_settings

logger = logging.getLogger(__name__)

# Per-provider call timeout. Kept short and non-configurable on purpose — a
# live demo can't afford a provider hanging for the SDK's default (10 min);
# failing over fast to the other provider is always better than waiting.
PER_PROVIDER_TIMEOUT_S = 20.0

# Errors worth retrying on the SAME provider once before failing over —
# transient network blips, not "this provider is down."
_SAME_PROVIDER_RETRYABLE = (
    anthropic.APIConnectionError,
    openai.APIConnectionError,
)


class LLMClient:
    """Provider-independent LLM client with automatic failover.

    Usage:
        client = LLMClient()
        response = await client.generate(
            prompt="Analyze these logs...",
            system="You are an incident response expert.",
        )
    """

    def __init__(self, provider: str | None = None):
        settings = get_settings()
        self.primary_provider = provider or settings.llm_provider
        self.secondary_provider = "openai" if self.primary_provider == "anthropic" else "anthropic"
        self._fallback_used = False

        self._anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or "missing",
            timeout=PER_PROVIDER_TIMEOUT_S,
        )
        self._openai_client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key or "missing",
            timeout=PER_PROVIDER_TIMEOUT_S,
        )

    async def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        """Generate a response from the LLM.

        Tries the primary provider first (with one same-provider retry on a
        transient connection error), falls back to the secondary provider on
        any failure, and falls back to a deterministic mock response if BOTH
        providers fail or neither has a configured API key — the pipeline
        must never crash mid-incident because of an LLM outage.
        """
        try:
            result = await self._call_with_retry(
                self.primary_provider, prompt, system, max_tokens, temperature
            )
            self._fallback_used = False
            return result
        except Exception as e:
            logger.warning(
                "Primary LLM provider (%s) failed: %s — falling back to %s",
                self.primary_provider, e, self.secondary_provider,
            )
            self._fallback_used = True
            try:
                return await self._call_with_retry(
                    self.secondary_provider, prompt, system, max_tokens, temperature
                )
            except Exception as e2:
                logger.error(
                    "Secondary LLM provider (%s) also failed: %s — using mock fallback",
                    self.secondary_provider, e2,
                )
                return self._mock_response(prompt)

    async def _call_with_retry(
        self,
        provider: str,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        try:
            return await self._call_provider(provider, prompt, system, max_tokens, temperature)
        except _SAME_PROVIDER_RETRYABLE as e:
            logger.info("Transient error on %s (%s) — retrying once before failover", provider, e)
            await asyncio.sleep(0.5)
            return await self._call_provider(provider, prompt, system, max_tokens, temperature)

    async def _call_provider(
        self,
        provider: str,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call a specific LLM provider. Falls back to a mock response if no
        API key is configured for that provider — lets the whole pipeline run
        in a demo/CI environment with zero keys set."""
        settings = get_settings()

        if provider == "anthropic":
            if not settings.anthropic_api_key:
                return self._mock_response(prompt)
            response = await self._anthropic_client.messages.create(
                model=settings.anthropic_model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system or anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(block.text for block in response.content if block.type == "text")

        elif provider == "openai":
            if not settings.openai_api_key:
                return self._mock_response(prompt)
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            response = await self._openai_client.chat.completions.create(
                model=settings.openai_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )
            return response.choices[0].message.content or ""

        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def _mock_response(self, prompt: str) -> str:
        """Deterministic mock response — used when no API key is configured
        for either provider, or both providers failed. Keeps the pipeline
        demoable/testable without network access or API keys."""
        return (
            "Based on the analysis of the provided data, "
            "the incident appears to be caused by the identified root cause. "
            "Confidence: 0.85."
        )

    @property
    def was_fallback_used(self) -> bool:
        """Check if the last call used the secondary provider."""
        return self._fallback_used


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client

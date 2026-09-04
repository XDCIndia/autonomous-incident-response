"""LLM client abstraction — provider-independent interface with failover.

Person 5 implements real Anthropic/OpenAI integration here.
For the foundation, a mock implementation is provided.

Supports:
- Anthropic (Claude)
- OpenAI (GPT-4)
- Automatic failover between providers
"""

from __future__ import annotations

import logging
from typing import Any

from backend.platform.config import get_settings

logger = logging.getLogger(__name__)


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

    async def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        """Generate a response from the LLM.

        Tries primary provider first, falls back to secondary on failure.
        """
        try:
            result = await self._call_provider(
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
            return await self._call_provider(
                self.secondary_provider, prompt, system, max_tokens, temperature
            )

    async def _call_provider(
        self,
        provider: str,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call a specific LLM provider.

        Mock implementation: returns a deterministic response.
        Person 5 implements real API calls here.
        """
        settings = get_settings()

        if provider == "anthropic":
            if not settings.anthropic_api_key:
                return self._mock_response(prompt)
            # Real implementation would use anthropic.AsyncAnthropic
            # client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            # response = await client.messages.create(...)
            return self._mock_response(prompt)

        elif provider == "openai":
            if not settings.openai_api_key:
                return self._mock_response(prompt)
            # Real implementation would use openai.AsyncOpenAI
            # client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            # response = await client.chat.completions.create(...)
            return self._mock_response(prompt)

        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def _mock_response(self, prompt: str) -> str:
        """Deterministic mock response for testing."""
        return (
            "Based on the analysis of the provided data, "
            "the incident appears to be caused by the identified root cause. "
            "Confidence: 0.85."
        )

    @property
    def was_fallback_used(self) -> bool:
        """Check if the last call used fallback."""
        return self._fallback_used


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client

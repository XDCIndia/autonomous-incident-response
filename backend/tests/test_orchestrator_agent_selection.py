"""Tests for IncidentOrchestrator's default agent selection (issue #5).

Verifies the orchestrator picks the deterministic mocks when no LLM provider
key is configured, and the real LLM-backed agents when one is — without ever
invoking .investigate()/.analyze() on them, so no network call happens either
way (type checks only).
"""

from __future__ import annotations

import pytest

from backend.agents.llm_agents import LLMArbiter, LLMLogInvestigator, LLMMetricInvestigator
from backend.agents.mock_agents import MockArbiter, MockLogInvestigator, MockMetricInvestigator
from backend.orchestrator.pipeline import IncidentOrchestrator, _llm_configured


def _clear_settings_cache():
    import backend.platform.config as config_module

    config_module._settings = None


@pytest.fixture(autouse=True)
def reset_settings():
    _clear_settings_cache()
    yield
    _clear_settings_cache()


class TestLlmConfiguredHelper:
    def test_false_when_no_keys_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        assert _llm_configured() is False

    def test_true_when_anthropic_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        assert _llm_configured() is True

    def test_true_when_only_openai_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        _clear_settings_cache()
        assert _llm_configured() is True


class TestOrchestratorDefaultAgentSelection:
    def test_defaults_to_mocks_when_no_keys_configured(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        orchestrator = IncidentOrchestrator()
        assert isinstance(orchestrator.log_investigator, MockLogInvestigator)
        assert isinstance(orchestrator.metric_investigator, MockMetricInvestigator)
        assert isinstance(orchestrator.arbiter, MockArbiter)

    def test_defaults_to_llm_agents_when_key_configured(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        orchestrator = IncidentOrchestrator()
        assert isinstance(orchestrator.log_investigator, LLMLogInvestigator)
        assert isinstance(orchestrator.metric_investigator, LLMMetricInvestigator)
        assert isinstance(orchestrator.arbiter, LLMArbiter)

    def test_explicit_injection_overrides_key_based_default(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "")
        _clear_settings_cache()
        explicit = MockLogInvestigator()
        orchestrator = IncidentOrchestrator(log_investigator=explicit)
        assert orchestrator.log_investigator is explicit

"""Unit tests for the environment-aware verification URL builders (issue:
env-aware verification URLs). Pure functions, no network/Docker — covers the
use_dns=True branch that no E2E test exercises (this repo's E2E suite always
runs with the backend host-side, IRAS_SERVICE_DNS unset)."""

from __future__ import annotations

from backend.simulator.health_checker import build_verify_urls, service_base_url


class TestServiceBaseUrl:
    def test_host_mode_uses_published_port(self):
        assert service_base_url("payment-service", use_dns=False) == "http://localhost:5001"
        assert service_base_url("db-service", use_dns=False) == "http://localhost:5004"

    def test_host_mode_unknown_service_defaults_to_5000(self):
        assert service_base_url("some-unmapped-service", use_dns=False) == "http://localhost:5000"

    def test_dns_mode_uses_container_name_and_in_container_port(self):
        assert service_base_url("payment-service", use_dns=True) == "http://iras-payment-service:5000"


class TestBuildVerifyUrls:
    def test_payment_service_recovery_action_adds_pay_probe(self):
        health_url, verify_urls = build_verify_urls("payment-service", "circuit_break", use_dns=False)
        assert health_url == "http://localhost:5001/health"
        assert verify_urls == ["http://localhost:5001/pay"]

    def test_payment_service_recovery_action_adds_pay_probe_dns_mode(self):
        health_url, verify_urls = build_verify_urls("payment-service", "reset_connection_pool", use_dns=True)
        assert health_url == "http://iras-payment-service:5000/health"
        assert verify_urls == ["http://iras-payment-service:5000/pay"]

    def test_non_recovery_action_has_no_pay_probe(self):
        _, verify_urls = build_verify_urls("payment-service", "rollback_deploy", use_dns=False)
        assert verify_urls == []

    def test_non_payment_service_never_gets_pay_probe(self):
        _, verify_urls = build_verify_urls("db-service", "circuit_break", use_dns=False)
        assert verify_urls == []

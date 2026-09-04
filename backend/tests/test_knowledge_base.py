"""Tests for the TF-IDF historical-incident knowledge base."""

from __future__ import annotations

import asyncio

import pytest

from backend.platform.knowledge_base import (
    _cosine,
    _tfidf_vector,
    _tokenize,
    index_resolved_incident,
    search_similar,
    seed_knowledge_base,
)
from backend.platform.storage import Storage
from backend.contracts import Incident, IncidentReport, IncidentState, SeverityLevel


@pytest.fixture
async def storage():
    s = Storage(db_path=":memory:")
    await s.init_db()  # seeds the knowledge base as part of startup
    yield s
    await s.close()


@pytest.fixture
async def empty_storage():
    s = Storage(db_path=":memory:")
    await s.init_db()
    conn = s._require_conn()
    await conn.execute("DELETE FROM knowledge_base")
    await conn.commit()
    yield s
    await s.close()


class TestSeeding:
    @pytest.mark.asyncio
    async def test_seed_count_matches_scenarios(self, storage):
        conn = storage._require_conn()
        async with conn.execute("SELECT COUNT(*) AS n FROM knowledge_base") as cursor:
            row = await cursor.fetchone()
        assert row["n"] == 8  # 2 per MVP scenario x 4 scenarios

    @pytest.mark.asyncio
    async def test_seed_is_idempotent(self, storage):
        await seed_knowledge_base(storage)
        await seed_knowledge_base(storage)
        conn = storage._require_conn()
        async with conn.execute("SELECT COUNT(*) AS n FROM knowledge_base") as cursor:
            row = await cursor.fetchone()
        assert row["n"] == 8

    @pytest.mark.asyncio
    async def test_all_four_scenarios_represented(self, storage):
        conn = storage._require_conn()
        async with conn.execute("SELECT DISTINCT root_cause FROM knowledge_base") as cursor:
            rows = await cursor.fetchall()
        root_causes = {row["root_cause"] for row in rows}
        assert root_causes == {"bad_deployment", "database_failure", "dependency_outage", "resource_exhaustion"}

    @pytest.mark.asyncio
    async def test_seed_entries_have_no_incident_id(self, storage):
        conn = storage._require_conn()
        async with conn.execute("SELECT incident_id FROM knowledge_base") as cursor:
            rows = await cursor.fetchall()
        assert all(row["incident_id"] is None for row in rows)


class TestSearch:
    @pytest.mark.asyncio
    async def test_finds_relevant_bad_deployment_incident(self, storage):
        results = await search_similar(storage, "deployment broke payment-service with errors")
        assert len(results) > 0
        assert results[0]["root_cause"] == "bad_deployment"

    @pytest.mark.asyncio
    async def test_finds_relevant_database_incident(self, storage):
        results = await search_similar(storage, "connection pool exhausted on the database")
        assert len(results) > 0
        assert results[0]["root_cause"] == "database_failure"

    @pytest.mark.asyncio
    async def test_finds_relevant_dependency_outage_incident(self, storage):
        results = await search_similar(storage, "downstream auth provider outage circuit breaker")
        assert len(results) > 0
        assert results[0]["root_cause"] == "dependency_outage"

    @pytest.mark.asyncio
    async def test_finds_relevant_resource_exhaustion_incident(self, storage):
        results = await search_similar(storage, "traffic spike exhausted cpu scale up")
        assert len(results) > 0
        assert results[0]["root_cause"] == "resource_exhaustion"

    @pytest.mark.asyncio
    async def test_results_are_ranked_by_similarity_desc(self, storage):
        results = await search_similar(storage, "circuit breaker dependency outage timeout", top_k=8)
        scores = [r["similarity"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_top_k_is_respected(self, storage):
        results = await search_similar(storage, "service incident outage database deployment", top_k=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_top_k_larger_than_corpus_returns_all_matches(self, storage):
        results = await search_similar(storage, "service incident outage database deployment scale", top_k=1000)
        assert len(results) <= 8

    @pytest.mark.asyncio
    async def test_nonsense_query_returns_no_false_positives(self, storage):
        results = await search_similar(storage, "xyzzy quantum banana zebra spaceship")
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_string_query_returns_empty_list(self, storage):
        results = await search_similar(storage, "")
        assert results == []

    @pytest.mark.asyncio
    async def test_whitespace_only_query_returns_empty_list(self, storage):
        results = await search_similar(storage, "   \t\n  ")
        assert results == []

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self, storage):
        lower = await search_similar(storage, "bad deployment payment errors")
        upper = await search_similar(storage, "BAD DEPLOYMENT PAYMENT ERRORS")
        assert [r["id"] for r in lower] == [r["id"] for r in upper]

    @pytest.mark.asyncio
    async def test_unicode_query_does_not_crash(self, storage):
        results = await search_similar(storage, "デプロイメント エラー 😀 pañgo")
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_sql_injection_like_query_is_safe(self, storage):
        results = await search_similar(storage, "'; DROP TABLE knowledge_base; --")
        assert isinstance(results, list)
        # Table must still exist and be queryable afterward.
        again = await search_similar(storage, "bad deployment")
        assert len(again) > 0

    @pytest.mark.asyncio
    async def test_very_long_query_does_not_crash(self, storage):
        long_query = "deployment error " * 5000
        results = await search_similar(storage, long_query)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_empty_knowledge_base_returns_empty_list(self, empty_storage):
        results = await search_similar(empty_storage, "anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_results_contain_expected_fields(self, storage):
        results = await search_similar(storage, "bad deployment payment errors")
        assert results
        r = results[0]
        for field in ("id", "incident_id", "service", "root_cause", "description", "resolved_via", "created_at", "similarity"):
            assert field in r

    @pytest.mark.asyncio
    async def test_concurrent_searches_do_not_interfere(self, storage):
        queries = [
            "bad deployment payment errors",
            "connection pool exhausted database",
            "circuit breaker dependency outage",
            "traffic spike cpu scale up",
        ] * 5
        results = await asyncio.gather(*[search_similar(storage, q) for q in queries])
        assert all(isinstance(r, list) and len(r) > 0 for r in results)


class TestIndexing:
    @pytest.mark.asyncio
    async def test_index_resolved_incident_directly(self, storage):
        incident = Incident(service_name="checkout-service", state=IncidentState.RESOLVED)
        incident.report = IncidentReport(incident_id=incident.id, service="checkout-service", impact="Checkout broke badly.")
        await index_resolved_incident(storage, incident)

        results = await search_similar(storage, "checkout broke badly")
        assert any(r["incident_id"] == incident.id for r in results)

    @pytest.mark.asyncio
    async def test_reindexing_same_incident_does_not_duplicate(self, storage):
        incident = Incident(service_name="checkout-service", state=IncidentState.RESOLVED)
        incident.report = IncidentReport(incident_id=incident.id, service="checkout-service", impact="text")
        await index_resolved_incident(storage, incident)
        await index_resolved_incident(storage, incident)
        await index_resolved_incident(storage, incident)

        conn = storage._require_conn()
        async with conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge_base WHERE incident_id = ?", (incident.id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["n"] == 1

    @pytest.mark.asyncio
    async def test_index_incident_with_no_arbiter_result_defaults_root_cause_unknown(self, storage):
        incident = Incident(service_name="mystery-service", state=IncidentState.RESOLVED)
        incident.report = IncidentReport(incident_id=incident.id, service="mystery-service", impact="Something broke.")
        await index_resolved_incident(storage, incident)

        conn = storage._require_conn()
        async with conn.execute(
            "SELECT root_cause FROM knowledge_base WHERE incident_id = ?", (incident.id,)
        ) as cursor:
            row = await cursor.fetchone()
        assert row["root_cause"] == "unknown"


class TestTfIdfMath:
    def test_tokenize_lowercases_and_splits(self):
        # Splits on underscores too — "payment_service" -> "payment", "service" —
        # which is desirable here since it lets "payment service" (space) match it.
        assert _tokenize("Bad-Deployment on Payment_Service!") == ["bad", "deployment", "on", "payment", "service"]

    def test_tokenize_empty_string(self):
        assert _tokenize("") == []

    def test_cosine_identical_vectors_is_one(self):
        v = {"a": 1.0, "b": 2.0}
        assert abs(_cosine(v, v) - 1.0) < 1e-9

    def test_cosine_orthogonal_vectors_is_zero(self):
        assert _cosine({"a": 1.0}, {"b": 1.0}) == 0.0

    def test_cosine_empty_vector_is_zero(self):
        assert _cosine({}, {"a": 1.0}) == 0.0
        assert _cosine({"a": 1.0}, {}) == 0.0

    def test_tfidf_vector_empty_tokens_is_empty(self):
        assert _tfidf_vector([], {"a": 1.0}) == {}

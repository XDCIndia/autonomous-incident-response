"""Knowledge base of historical incidents (Person 5 / Platform-Integration).

Not part of the original foundation scaffold — added to satisfy the
"Knowledge Base / historical incidents" line item in the Platform/Integration
role. Lives in its own table in the same SQLite file `storage.py` already
owns, so there's exactly one database file for the whole app.

Two entry points other code actually needs:
- `index_resolved_incident(storage, incident)` — called automatically by
  `Storage.save_incident()` whenever an incident reaches RESOLVED with a
  report attached. No other role has to remember to call this.
- `search_similar(storage, query, top_k=3)` — used by the `/knowledge-base/
  search` API endpoint, and available for the Reporter agent (Person 1) to
  call directly if they want to cite similar past incidents in a report.

Similarity is plain TF-IDF cosine similarity over (scenario/root-cause +
service + a short description) — deliberately not an embedding-API call:
zero network dependency, zero chance of that call failing live during a
demo, and a hackathon-scale knowledge base (a few dozen rows) doesn't need
anything fancier to give useful "we've seen this before" results.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from backend.contracts import Incident

if TYPE_CHECKING:
    from backend.platform.storage import Storage

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# 2 synthetic past incidents per MVP scenario, seeded once so the knowledge
# base isn't empty for the very first live demo incident.
_SEED_INCIDENTS: list[dict[str, Any]] = [
    {
        "service": "payment-service", "root_cause": "bad_deployment",
        "description": "Deployment v2.1.0 introduced a null pointer bug, error rate spiked to 40% on payment-service.",
        "resolved_via": "rollback_deploy",
    },
    {
        "service": "checkout-service", "root_cause": "bad_deployment",
        "description": "Bad config in a canary release broke checkout-service request validation.",
        "resolved_via": "rollback_deploy",
    },
    {
        "service": "inventory-service", "root_cause": "database_failure",
        "description": "Connection pool exhaustion on inventory-service after a slow query held connections open.",
        "resolved_via": "reset_connection_pool",
    },
    {
        "service": "order-service", "root_cause": "database_failure",
        "description": "Primary DB failover caused order-service to see stale connections and timeouts.",
        "resolved_via": "reset_connection_pool",
    },
    {
        "service": "api-gateway", "root_cause": "dependency_outage",
        "description": "Downstream auth provider outage caused api-gateway requests to hang and time out.",
        "resolved_via": "circuit_break",
    },
    {
        "service": "notification-service", "root_cause": "dependency_outage",
        "description": "Third-party email provider outage backed up notification-service's send queue.",
        "resolved_via": "circuit_break",
    },
    {
        "service": "search-service", "root_cause": "resource_exhaustion",
        "description": "Traffic spike exhausted CPU on search-service, latency climbed past 2 seconds.",
        "resolved_via": "scale_up",
    },
    {
        "service": "recommendation-service", "root_cause": "resource_exhaustion",
        "description": "Memory pressure from an unbounded cache caused recommendation-service to slow down under load.",
        "resolved_via": "scale_up",
    },
]

_SEEDED = False


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _tf(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = len(tokens) or 1
    return {term: count / total for term, count in counts.items()}


def _build_idf(docs_tokens: list[list[str]]) -> dict[str, float]:
    n_docs = len(docs_tokens) or 1
    df: Counter[str] = Counter()
    for tokens in docs_tokens:
        for term in set(tokens):
            df[term] += 1
    return {term: math.log((n_docs + 1) / (count + 1)) + 1.0 for term, count in df.items()}


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf = _tf(tokens)
    return {term: weight * idf.get(term, 0.0) for term, weight in tf.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    numerator = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return numerator / (norm_a * norm_b)


def _indexed_text(service: str, root_cause: str, description: str) -> str:
    return f"{service} {root_cause} {description}"


async def _init_table(storage: "Storage") -> None:
    conn = storage._require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT,
            seed_key TEXT UNIQUE,
            service TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            description TEXT NOT NULL,
            resolved_via TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await conn.commit()


async def seed_knowledge_base(storage: "Storage") -> None:
    """Seed the knowledge base with synthetic past incidents.

    Each seed row carries a stable, unique `seed_key` ("seed-0".."seed-7")
    and is inserted with INSERT OR IGNORE — genuinely idempotent at the
    database level, not just "check count first, then insert." The
    count-then-insert version of this had a real race: two Storage
    instances calling init_db() concurrently on the same brand-new file
    could both see count=0 and both insert, producing 16 rows instead of 8
    (reproduced directly, outside pytest). INSERT OR IGNORE with a UNIQUE
    seed_key makes a second, concurrent seeding attempt a harmless no-op
    regardless of timing.
    """
    global _SEEDED
    await _init_table(storage)
    conn = storage._require_conn()

    for i, entry in enumerate(_SEED_INCIDENTS):
        await conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_base (incident_id, seed_key, service, root_cause, description, resolved_via)
            VALUES (NULL, ?, ?, ?, ?, ?)
            """,
            (f"seed-{i}", entry["service"], entry["root_cause"], entry["description"], entry["resolved_via"]),
        )
    await conn.commit()
    _SEEDED = True
    logger.info("KnowledgeBase: seeded %d historical incidents", len(_SEED_INCIDENTS))


async def index_resolved_incident(storage: "Storage", incident: Incident) -> None:
    """Add a real resolved incident to the knowledge base for future similarity search.

    Idempotent per incident_id — `Storage.save_incident()` (and the pipeline
    that calls it) may legitimately be invoked more than once for the same
    already-resolved incident; re-indexing must replace, not duplicate.
    """
    await _init_table(storage)
    conn = storage._require_conn()
    root_cause = incident.arbiter_result.root_cause if incident.arbiter_result else "unknown"
    description = incident.report.impact if incident.report else f"Incident on {incident.service_name}"
    resolved_via = incident.remediation_result.action if incident.remediation_result else "unknown"

    await conn.execute("DELETE FROM knowledge_base WHERE incident_id = ?", (incident.id,))
    await conn.execute(
        """
        INSERT INTO knowledge_base (incident_id, service, root_cause, description, resolved_via)
        VALUES (?, ?, ?, ?, ?)
        """,
        (incident.id, incident.service_name, root_cause, description, resolved_via),
    )
    await conn.commit()
    logger.info("KnowledgeBase: indexed resolved incident %s", incident.id)


async def search_similar(storage: "Storage", query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Return up to `top_k` historical incidents most similar to `query`,
    ranked by TF-IDF cosine similarity. Returns an empty list (never raises)
    if the knowledge base is empty or nothing scores above zero."""
    await _init_table(storage)
    conn = storage._require_conn()

    async with conn.execute(
        "SELECT id, incident_id, service, root_cause, description, resolved_via, created_at "
        "FROM knowledge_base ORDER BY id ASC"
    ) as cursor:
        rows = await cursor.fetchall()
    if not rows:
        return []

    docs_tokens = [
        _tokenize(_indexed_text(row["service"], row["root_cause"], row["description"]))
        for row in rows
    ]
    idf = _build_idf(docs_tokens)
    doc_vectors = [_tfidf_vector(tokens, idf) for tokens in docs_tokens]
    query_vector = _tfidf_vector(_tokenize(query), idf)

    scored = [
        (_cosine(query_vector, doc_vec), row)
        for doc_vec, row in zip(doc_vectors, rows)
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    results = []
    for score, row in scored[:top_k]:
        if score <= 0:
            continue
        results.append(
            {
                "id": row["id"],
                "incident_id": row["incident_id"],
                "service": row["service"],
                "root_cause": row["root_cause"],
                "description": row["description"],
                "resolved_via": row["resolved_via"],
                "created_at": row["created_at"],
                "similarity": round(score, 4),
            }
        )
    return results

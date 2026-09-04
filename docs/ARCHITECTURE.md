# Architecture

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Next.js Dashboard                                                │
│  - Control panel: 4 "Inject Fault" buttons (MVP scenarios)        │
│  - Live incident timeline (WebSocket)                             │
│  - Incident detail view                                           │
└───────────────────────────┬────────────────────────────────────────┘
                            │ REST + WebSocket
┌───────────────────────────▼────────────────────────────────────────┐
│  Python FastAPI service                                            │
│                                                                      │
│  api/              REST + WebSocket endpoints                       │
│  orchestrator/     Pipeline orchestration                           │
│  agents/           Investigation, arbiter, severity, reporter       │
│  simulator/        Fault injection + service graph                  │
│  remediation/      Fixed action remediation engine                  │
│  platform/         LLM client, SQLite, config, event bus            │
│  contracts/        Shared Pydantic models (integration boundary)    │
└──────────────────────────────────────────────────────────────────┘
```

## Core Pipeline Flow

```
External Alert / Fault
        ↓
    Detection (rule-based)
        ↓
    Log + Metric Investigation (parallel, LLM-backed)
        ↓
    Arbiter (reconciles both investigators)
        ↓
    Confidence >= threshold? ──no──→ Retry (wider window)
        │                                  │
       yes                            retry exhausted
        │                                  │
        ↓                                  ↓
    Severity                           ASSIST tier
        ↓                              (recommend only)
    Autonomy Decision
        ↓
    Remediation (fixed action set)
        ↓
    Verification
        ↓
    Incident Report
```

## Integration Boundary

**`backend/contracts/` is the ONLY integration boundary.**

- Orchestrator depends on `LogInvestigationResult`, `MetricInvestigationResult`, etc.
- Orchestrator does NOT import from `agents/log_investigator.py` directly.
- Frontend communicates through API/WebSocket contracts only.
- Simulator exposes stable interfaces (`inject_*()` functions).

## Key Design Decisions

1. **Detector stays rule-based** — instant, reliable, no LLM latency
2. **Remediation uses fixed action set** — LLM picks from allowed actions, never arbitrary code
3. **Three-tier autonomy** — confidence × severity determines: assist / semi-autonomous / autonomous
4. **Dual-angle investigation** — log + metric investigators run in parallel, arbiter reconciles
5. **SQLite initially** — lightweight, no external dependencies for hackathon

## Files Created

```
backend/
├── contracts/          # Shared Pydantic models (INTEGRATION BOUNDARY)
│   └── models.py       # All shared types
├── agents/             # Agent base classes + mock implementations
│   ├── base.py         # Abstract interfaces
│   └── mock_agents.py  # Deterministic mock implementations
├── orchestrator/       # Pipeline orchestration
│   └── pipeline.py     # Full pipeline with mock agents
├── simulator/          # Fault injection
│   ├── scenarios.py    # 4 MVP scenarios
│   └── service_graph.py # Service dependency graph
├── remediation/        # Fixed action remediation
│   └── actions.py      # Remediation engine
├── platform/           # Infrastructure abstractions
│   ├── config.py       # Settings
│   ├── llm_client.py   # LLM abstraction with failover
│   ├── storage.py      # SQLite storage
│   └── events.py       # WebSocket event bus
├── api/                # FastAPI endpoints
│   └── app.py          # REST + WebSocket
├── tests/              # Tests
│   ├── test_contracts.py
│   ├── test_agents.py
│   ├── test_remediation.py
│   └── e2e/test_pipeline.py
└── main.py             # Entry point
```

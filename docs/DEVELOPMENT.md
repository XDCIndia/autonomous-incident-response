# Development Guide

## Quick Start

### Backend

```bash
# Clone
git clone https://github.com/XDCIndia/autonomous-incident-response.git
cd autonomous-incident-response

# Install Python 3.11+
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Start backend
python -m backend.main
# → http://localhost:8000

# Trigger an incident
curl -X POST http://localhost:8000/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{"service_name": "payment-service", "scenario": "bad_deployment"}'

# Check health
curl http://localhost:8000/health
```

### Frontend

```bash
cd frontend

# Install Node.js 18+
npm install

# Start dev server
npm run dev
# → http://localhost:3000
```

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/incidents/trigger` | Trigger a new incident |
| `GET` | `/incidents` | List recent incidents |
| `GET` | `/incidents/{id}` | Get incident details |
| `GET` | `/incidents/{id}/timeline` | Get incident timeline |
| `WS` | `/ws/incidents/{id}` | WebSocket stream of timeline events |

### Trigger Request

```json
{
  "service_name": "payment-service",
  "scenario": "bad_deployment",
  "target_url": "https://payment.example.com"
}
```

### Available Scenarios

- `bad_deployment` — New version causes errors and latency spike
- `database_failure` — Connection pool exhaustion
- `dependency_outage` — External dependency stops responding
- `resource_exhaustion` — CPU/memory leak causing degradation

## Developer Ownership

### Person 1 — AI / Agents

**Your directory:** `backend/agents/`

**Your files:**
- `backend/agents/base.py` — Abstract interfaces (already defined)
- `backend/agents/log_investigator.py` — Your implementation
- `backend/agents/metric_investigator.py` — Your implementation
- `backend/agents/arbiter.py` — Your implementation
- `backend/agents/severity.py` — Your implementation
- `backend/agents/reporter.py` — Your implementation

**Your dependencies:**
- `backend.contracts` — Shared models (import from here)
- `backend.platform.llm_client` — LLM abstraction

**Do NOT import from:**
- `backend.orchestrator.*`
- `backend.simulator.*`
- `backend.remediation.*`

---

### Person 2 — Orchestrator

**Your directory:** `backend/orchestrator/`

**Your files:**
- `backend/orchestrator/pipeline.py` — Core orchestration logic
- `backend/orchestrator/state.py` — Incident state management
- `backend/orchestrator/detection.py` — Detection logic
- `backend/orchestrator/timeline.py` — Timeline management

**Your dependencies:**
- `backend.contracts` — Shared models
- `backend.agents` — Agent interfaces (NOT internal implementations)
- `backend.platform` — Storage, events

**Do NOT import from:**
- `backend.agents.log_investigator` (use the interface)
- `backend.simulator.*`
- `backend.remediation.*`

---

### Person 3 — Simulator / Remediation

**Your directory:** `backend/simulator/` + `backend/remediation/`

**Your files:**
- `backend/simulator/scenarios.py` — Fault injection scripts
- `backend/simulator/service_graph.py` — Service dependency graph
- `backend/remediation/actions.py` — Remediation execution

**Your dependencies:**
- `backend.contracts` — Shared models

**Do NOT import from:**
- `backend.agents.*`
- `backend.orchestrator.*`
- `backend.platform.*`

---

### Person 4 — Frontend

**Your directory:** `frontend/`

**Your files:**
- Dashboard page (`/`)
- Incident detail page (`/incidents/[id]`)
- Live timeline component
- WebSocket integration

**Your dependencies:**
- REST API (`/incidents`, `/incidents/{id}`, `/incidents/{id}/timeline`)
- WebSocket (`ws://localhost:8000/ws/incidents/{id}`)

**Do NOT import from:**
- Any Python backend module directly

---

### Person 5 — Platform / Integration

**Your directory:** `backend/platform/` + `backend/api/` + `backend/tests/`

**Your files:**
- `backend/platform/llm_client.py` — Real Anthropic/OpenAI integration
- `backend/platform/storage.py` — Real SQLite with aiosqlite
- `backend/platform/config.py` — Configuration
- `backend/platform/events.py` — WebSocket broadcast
- `backend/api/app.py` — API routes
- `backend/tests/` — E2E and unit tests

**Your dependencies:**
- `backend.contracts` — Shared models
- `backend.orchestrator` — Pipeline (for E2E tests)

**Do NOT import from:**
- `backend.agents.*` (only interfaces)
- `backend.simulator.*`
- `backend.remediation.*`

## Testing

```bash
# Run all tests
pytest

# Run contract tests only
pytest backend/tests/test_contracts.py

# Run agent tests
pytest backend/tests/test_agents.py

# Run remediation tests
pytest backend/tests/test_remediation.py

# Run E2E tests
pytest backend/tests/e2e/ -v

# Run with coverage
pytest --cov=backend --cov-report=html
```

## Architecture Principles

1. **Shared contracts are the integration boundary** — never bypass them
2. **Mock implementations first** — replace with real logic incrementally
3. **Every stage appends a TimelineEvent** — for observability
4. **Fixed remediation actions** — never allow arbitrary code execution
5. **SQLite initially** — no external dependencies for hackathon

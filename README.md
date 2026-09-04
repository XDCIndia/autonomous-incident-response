# Autonomous Enterprise Incident Response

A generic enterprise incident-response system that detects, investigates, and remediates service incidents autonomously.

## Purpose

When an incident occurs (bad deployment, dependency outage, database failure, resource exhaustion), this system:

1. **Detects** the anomaly from telemetry signals
2. **Investigates** using parallel log and metric analysis
3. **Arbitrates** to reconcile findings and determine root cause
4. **Assesses** severity and blast radius
5. **Decides** autonomy level (assist / semi-autonomous / autonomous)
6. **Remediates** using a fixed, safe action set
7. **Verifies** that remediation worked
8. **Reports** with root cause, impact, and prevention recommendations

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Next.js Dashboard                                                │
│  - Control panel: 4 "Inject Fault" buttons                        │
│  - Live incident timeline (WebSocket)                             │
│  - Incident detail view                                           │
└───────────────────────────┬────────────────────────────────────────┘
                            │ REST + WebSocket
┌───────────────────────────▼────────────────────────────────────────┐
│  Python FastAPI Backend                                            │
│  contracts/ → agents/ → orchestrator/ → simulator/ → remediation/  │
└──────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Backend

```bash
# Clone
git clone https://github.com/XDCIndia/autonomous-incident-response.git
cd autonomous-incident-response

# Setup Python
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Start backend
python -m backend.main
# → http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

### Trigger an Incident

```bash
# Bad deployment scenario
curl -X POST http://localhost:8000/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{"service_name": "payment-service", "scenario": "bad_deployment"}'

# Database failure
curl -X POST http://localhost:8000/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{"service_name": "inventory-service", "scenario": "database_failure"}'
```

### Check Health

```bash
curl http://localhost:8000/health
```

## Fault Scenarios

| Scenario | What Happens | Root Cause | Remediation |
|---|---|---|---|
| **Bad Deployment** | New version causes errors and latency spike | `bad_deployment` | `rollback_deploy` |
| **Database Failure** | Connection pool exhaustion, queries timeout | `database_failure` | `reset_connection_pool` |
| **Dependency Outage** | External dependency stops responding | `dependency_outage` | `circuit_break` |
| **Resource Exhaustion** | CPU/memory leak causing degradation | `resource_exhaustion` | `scale_up` |

## Repository Structure

```
autonomous-incident-response/
├── backend/
│   ├── contracts/          # Shared Pydantic models (INTEGRATION BOUNDARY)
│   ├── agents/             # Agent base classes + implementations
│   ├── orchestrator/       # Pipeline orchestration
│   ├── simulator/          # Fault injection + service graph
│   ├── remediation/        # Fixed action remediation engine
│   ├── platform/           # LLM client, SQLite, config, events
│   ├── api/                # FastAPI REST + WebSocket endpoints
│   ├── tests/              # Unit + E2E tests
│   └── main.py             # Entry point
├── frontend/               # Next.js + TypeScript dashboard
├── docs/
│   ├── ARCHITECTURE.md     # System architecture
│   ├── CONTRACTS.md        # Shared model reference
│   └── DEVELOPMENT.md      # Developer guide
├── .env.example
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## Developer Ownership

| Person | Area | Directory |
|---|---|---|
| **Person 1** | AI / Agents | `backend/agents/` |
| **Person 2** | Orchestrator | `backend/orchestrator/` |
| **Person 3** | Simulator / Remediation | `backend/simulator/` + `backend/remediation/` |
| **Person 4** | Frontend | `frontend/` |
| **Person 5** | Platform / Integration | `backend/platform/` + `backend/api/` + `backend/tests/` |

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for detailed ownership and dependency rules.

## Integration Boundary

**`backend/contracts/` is the ONLY integration boundary.**

All developers import shared models from `backend.contracts`. No developer should import internal implementations from another developer's directory.

```python
# ✅ GOOD
from backend.contracts import LogInvestigationResult

# ❌ BAD
from backend.agents.log_investigator import _internal_var
```

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/incidents/trigger` | Trigger a new incident |
| `GET` | `/incidents` | List recent incidents |
| `GET` | `/incidents/{id}` | Get incident details |
| `GET` | `/incidents/{id}/timeline` | Get incident timeline |
| `WS` | `/ws/incidents/{id}` | WebSocket stream of timeline events |

## Testing

```bash
# Run all tests
pytest

# Run E2E tests
pytest backend/tests/e2e/ -v

# Run with coverage
pytest --cov=backend --cov-report=html
```

## License

MIT

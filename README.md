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

When the full stack is running (docker-compose), the backend also drives a set
of **mock Web3 services** — `payment-service`, `rpc-service-primary/secondary`,
`db-service` — plus **Toxiproxy** for real fault injection (timeouts, disabled
proxies) and remediation (circuit break, rollback, connection-pool reset).

## Quick Start

### Option A — Docker Compose (recommended, full stack)

```bash
# Clone
git clone https://github.com/XDCIndia/autonomous-incident-response.git
cd autonomous-incident-response

# Copy the sample env (optionally add LLM keys)
cp .env.example .env

# Build and start everything
docker compose up -d --build
```

| Service | URL |
|---|---|
| **Dashboard (frontend)** | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API docs (Swagger UI) | http://localhost:8000/docs |
| payment-service | http://localhost:5001 |
| Toxiproxy API | http://localhost:8474 |

The backend runs **inside** the compose network: it drives the real
Docker/Toxiproxy environment (`REAL_ENV=auto`) and its health verification
reaches services by Docker DNS name (`IRAS_SERVICE_DNS=true`, e.g.
`http://iras-payment-service:5000`) instead of published host ports
(`http://localhost:5001`). Backend code is bind-mounted and hot-reloads via
uvicorn `--reload`; the frontend runs `next dev` with hot reload too.

### Option B — Local backend only (mock mode)

Run the backend on the host without Docker; when no IRAS containers are
running it stays in mock mode (deterministic mock signals + in-memory
remediation), and verification probes use the published host ports:

```bash
# Setup Python
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Start backend
python -m backend.main
# → http://localhost:8000

# (Optional) Frontend on the host
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

### Trigger an Incident

```bash
# Bad deployment scenario (real stack, or mock when no Docker)
curl -X POST http://localhost:8000/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{"service_name": "payment-service", "scenario": "bad_deployment"}'

# Database failure
curl -X POST http://localhost:8000/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{"service_name": "payment-service", "scenario": "database_failure"}'

# Direct fault injection + remediation against the real stack
curl -X POST http://localhost:8000/faults/inject \
  -H "Content-Type: application/json" \
  -d '{"scenario": "dependency_outage", "service_name": "payment-service", "parameters": {}}'
```

The `/faults/inject` response carries the metadata (e.g. `proxy_name`,
`toxic_name`, `previous_config`) that `/remediation/execute` needs to undo the
fault; remediation responses include a real health verification
(`verification.verified`).

### Check Health

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/services/health?service=payment-service"
```

## Fault Scenarios

| Scenario | What Happens | Root Cause | Remediation |
|---|---|---|---|
| **Bad Deployment** | New version causes errors and latency spike | `bad_deployment` | `rollback_deploy` |
| **Database Failure** | Connection pool exhaustion, queries timeout | `database_failure` | `reset_connection_pool` |
| **Dependency Outage** | External dependency stops responding | `dependency_outage` | `circuit_break` |
| **Resource Exhaustion** | CPU/memory leak causing degradation | `resource_exhaustion` | `scale_up` |

## Environment Variables

See `.env.example` for the full list. Key settings:

| Variable | Default | Description |
|---|---|---|
| `REAL_ENV` | `auto` | `auto` / `on` / `off` — whether `/incidents/trigger` drives the real Docker/Toxiproxy stack |
| `IRAS_SERVICE_DNS` | `false` | `true` when the backend runs inside the compose network — verification uses `http://iras-<service>:5000` instead of `http://localhost:<host-port>` |
| `API_KEY` | *(empty)* | Optional key required on mutating endpoints via the `X-API-Key` header |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Browser origins allowed to call the API (never `*`) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | *(empty)* | LLM provider keys; when unset the pipeline uses deterministic mock agents |

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
| `GET` | `/services/health?service=` | Docker health of an IRAS-managed service |
| `POST` | `/incidents/trigger` | Trigger a new incident (full autonomous pipeline) |
| `GET` | `/incidents` | List recent incidents |
| `GET` | `/incidents/{id}` | Get incident details |
| `GET` | `/incidents/{id}/timeline` | Get incident timeline |
| `GET` | `/incidents/{id}/approval` | Check pending approval status |
| `POST` | `/incidents/{id}/approve` | Approve a pending SEMI_AUTONOMOUS remediation |
| `POST` | `/incidents/{id}/reject` | Reject a pending remediation |
| `POST` | `/faults/inject` | Inject a real fault (`bad_deployment`, `database_failure`, `dependency_outage`, `resource_exhaustion`) |
| `POST` | `/remediation/execute` | Execute a remediation action, then verify recovery |
| `GET` | `/knowledge-base/search?query=` | Search historical incidents by similarity |
| `WS` | `/ws/incidents/{id}` | WebSocket stream of timeline events |

Mutating endpoints (`/faults/inject`, `/remediation/execute`,
`/incidents/trigger`, approve/reject) require the `X-API-Key` header **only**
when `API_KEY` is set; auth is disabled by default for local dev.

## Testing

```bash
# Full suite (unit + hermetic)
pytest backend/tests/unit backend/tests/test_storage.py

# Docker-scenario E2Es — require `docker compose up -d` first, run on the host
pytest backend/tests/test_bad_deployment_e2e.py \
       backend/tests/test_database_failure_e2e.py \
       backend/tests/test_dependency_outage_e2e.py \
       backend/tests/test_toxiproxy_reset_e2e.py -v

# Coverage
pytest --cov=backend --cov-report=html
```

Notes:
- `backend/tests/unit/` and the plain unit tests are hermetic — no Docker or
  network needed.
- The `*_e2e.py` tests hit `http://localhost:8000` and drive the real stack,
  so they are skipped when the compose services are not running.
- `backend/tests/test_live_server_load.py` runs concurrent real incidents
  against a live backend and needs the full stack up.

## License

MIT
# Codebase Audit: Autonomous Enterprise Incident Response

## 1. CURRENT PROJECT STRUCTURE
Here are the important directories and files in the repository:
- `backend/api/app.py`: FastAPI application setting up REST and WebSocket endpoints for incident triggers and live timelines.
- `backend/contracts/models.py`: Shared Pydantic models defining the integration boundary (`Incident`, `TimelineEvent`, `TelemetryEvent`, etc.).
- `backend/orchestrator/pipeline.py`: A procedural async python orchestrator that runs the incident response pipeline from detection to reporting.
- `backend/agents/mock_agents.py`: Stubbed implementations of the AI agents that return deterministic results based on hardcoded rules.
- `backend/simulator/scenarios.py` & `service_graph.py`: Stubs for injecting telemetry events to simulate faults and define a basic dependency graph.
- `backend/remediation/actions.py`: Mock remediation engine that updates an in-memory dictionary to simulate recovery.
- `backend/platform/storage.py`: In-memory storage mock providing an SQLite-like interface for incidents and timelines.
- `frontend/`: Next.js frontend with a dashboard (`app/page.tsx`) to trigger incidents and view live WebSocket timelines (`app/incidents/[id]/page.tsx`).
- `docker-compose.yml` & `Dockerfile`: Setup for running the frontend (Next.js) and backend (FastAPI) together.

## 2. IRAS BASE
The current codebase is heavily based on the IRAS template, which provides a full end-to-end skeleton.
- **Present:** The API routing, WebSocket broadcasting, frontend dashboard, shared data contracts (Pydantic), and the conceptual pipeline (Detect -> Report) are all present and fully integrated.
- **Missing/Changed:** The core components are mocked. The agents are not using LLMs, the database is strictly in-memory (no actual SQLite), the orchestrator is procedural (not LangGraph), and the simulator only generates static Pydantic events rather than running real services.

## 3. PROBLEM STATEMENT COVERAGE
- Fault: ⚠️ partially supported (Mock scenarios exist, returning static telemetry events)
- Detect: ✅ already supported (FastAPI endpoint `/incidents/trigger` handles detection)
- Investigate: ⚠️ partially supported (Mock log and metric investigators)
- Arbiter: ⚠️ partially supported (Mock arbiter that just checks if investigators agree)
- Severity: ⚠️ partially supported (Mock agent calculating severity based on hardcoded rules)
- Remediate: ⚠️ partially supported (Mock engine that updates an internal python dictionary)
- Verify: ⚠️ partially supported (Mock verification that assumes success if remediation succeeded)
- Report: ⚠️ partially supported (Generates a static report from pipeline data)

## 4. TEAM ROLE COVERAGE
**1. AI / Agents**
- **Existing Files:** `backend/agents/mock_agents.py`, `backend/agents/base.py`
- **Reusable:** The base interfaces (`LogInvestigator`, `MetricInvestigator`, etc.) are good integration boundaries.
- **Missing:** Real LLM integration (OpenAI/Anthropic), Langchain usage, structured output parsers, real confidence scoring.
- **Conflicts:** Mock agents currently return static data.

**2. Orchestrator / Incident Flow**
- **Existing Files:** `backend/orchestrator/pipeline.py`
- **Reusable:** The logic of timeline management and state transitions is correct.
- **Missing:** LangGraph orchestration is completely missing.
- **Conflicts:** The current orchestrator is procedural Python (`await self._investigate()`). If the architecture demands LangGraph, this file will need a complete rewrite into a LangGraph `StateGraph`.

**3. Simulator / Remediation**
- **Existing Files:** `backend/simulator/scenarios.py`, `backend/remediation/actions.py`
- **Reusable:** The scenarios outline the faults needed, and the remediation engine defines the allowed actions.
- **Missing:** The Web3 service environment is missing. There are no actual deterministic logs or metrics being generated, just hardcoded `TelemetryEvent` objects. Telemetry and Remediation APIs do not exist.
- **Conflicts:** The simulator does not run actual services, making real remediation (e.g. docker restarts, scaling) impossible without changing the simulator approach.

**4. Frontend**
- **Existing Files:** `frontend/app/page.tsx`, `frontend/app/incidents/[id]/page.tsx`
- **Reusable:** The entire frontend is highly reusable, including WebSocket integration.
- **Missing:** Advanced views for agent findings, confidence scoring visuals, and the Knowledge Base UI.
- **Conflicts:** None.

**5. Platform / Integration**
- **Existing Files:** `backend/platform/storage.py`, `backend/api/app.py`
- **Reusable:** The API endpoints and storage interfaces.
- **Missing:** Real SQLite implementation, OpenAI/Anthropic failover, Knowledge Base storage.
- **Conflicts:** `storage.py` is in-memory and will lose data on restart.

## 5. SIMULATOR/REMEDIATION AUDIT
The current template **does not** have a real foundation for the simulator yet, it is strictly mocked in Python:
- **Web3 service simulation:** ❌ missing
- **Service dependency graph:** ⚠️ partially supported (metadata only in `service_graph.py`, no real services)
- **Deterministic fault injection:** ⚠️ partially supported (static event lists returned by functions)
- **Logs:** ❌ missing (no real logs, just string metadata in static events)
- **Metrics:** ❌ missing (no real metrics, just float values in static events)
- **RPC failure:** ❌ missing (simulated by events)
- **DB failure:** ❌ missing (simulated by events)
- **Bad deployment:** ❌ missing (simulated by events)
- **Resource exhaustion:** ❌ missing (simulated by events)
- **Rollback:** ❌ missing (simulated by dict update)
- **Restart:** ❌ missing (simulated by dict update)
- **Scale:** ❌ missing (simulated by dict update)
- **Circuit-break/switch RPC:** ❌ missing (simulated by dict update)
- **Telemetry APIs:** ❌ missing
- **Remediation APIs:** ❌ missing

## 6. SHARED INTERFACES
- **Location:** `backend/contracts/models.py`
- **Assessment:** Yes, there is a comprehensive shared contract based on Pydantic models (`Incident`, `TimelineEvent`, `TelemetryEvent`, `IncidentState`, `PipelineStage`, and results for each stage).
- **Suitability:** Highly suitable. It serves as an excellent, strict boundary for the team to integrate against.

## 7. DOCKER
- **Dockerfile:** `backend/Dockerfile` uses `python:3.11-slim`, exposing port 8000.
- **docker-compose.yml:** Orchestrates the backend (FastAPI on 8000) and frontend (Next.js on 3000) using local volumes for hot-reloading.
- **Environment Variables:** Frontend correctly takes `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL`.
- **Conflicts:** The compose file lacks containers for the "Web3 service environment" simulator. Additionally, if the team switches to a real database that isn't SQLite (e.g., Postgres or Redis for LangGraph state), it will need to be added.

## 8. SQLITE
- **Current State:** Storage (`backend/platform/storage.py`) is completely **IN-MEMORY** using python dictionaries (`self._incidents`, `self._timelines`).
- **Capabilities:** It currently has the *interfaces* to support incidents, timeline events, logs, and metrics (attached to the incident state). However, because it is in-memory, it cannot support historical incidents or a Knowledge Base across server restarts.
- **Action Required:** Needs to be rewritten to use a real SQLite driver like `aiosqlite`.

## 9. RECOMMENDED CHANGE PLAN
To reach the MVP (BAD DEPLOYMENT → DETECT → INVESTIGATE → RCA → REMEDIATION → VERIFY → REPORT):

1. **Storage (Platform):** Update `backend/platform/storage.py` to use a real SQLite file to enable state persistence and the Knowledge Base.
2. **Orchestrator (Orchestrator):** Rewrite `backend/orchestrator/pipeline.py` to use LangGraph as the orchestrator to align with the architecture constraints.
3. **LLM Agents (Agents):** Replace `MockLogInvestigator` and `MockMetricInvestigator` with real LLM implementations (OpenAI/Anthropic).
4. **LLM Arbiter (Agents):** Replace `MockArbiter` with a real LLM implementation to properly merge findings.
5. **Simulator (Simulator):** Implement the Web3 mock services (either as actual Docker containers or an advanced stateful Python simulator with Telemetry APIs).
6. **Remediation (Remediation):** Update `actions.py` to execute real remediation (e.g., hitting the simulator's Remediation APIs).
7. **Frontend (Frontend):** Expand the dashboard to visualize Agent Findings, Confidence, and the final Report.

## 10. RISKS / RED FLAGS
1. **Procedural Orchestrator:** `backend/orchestrator/pipeline.py` is written as standard async procedural code. Transitioning this to LangGraph is a significant architectural shift that needs to happen *before* agents are heavily developed, otherwise integration will be painful.
2. **Mock Simulator Limits:** The current simulator instantly generates all events for a fault. Real incident response involves investigating telemetry *over time*. The simulator needs to act like a real system or the agents won't actually be learning how to investigate.
3. **In-Memory Storage:** Any work done on the Knowledge Base will be blocked until `storage.py` is backed by a real SQLite file.

## 11. FINAL VERDICT
- **Is the current template aligned with our problem statement?** Conceptually, yes. The data flow, stages, and UI perfectly match the problem statement. However, technically, it is a mock skeleton lacking the required AI, LangGraph, and Simulator depth.
- **What should we KEEP?** Keep the shared Pydantic models (`contracts`), the FastAPI router structure, the WebSocket broadcasting setup, and the Next.js frontend.
- **What should we CHANGE BEFORE FEATURE DEVELOPMENT?** 
  1. Rewrite `pipeline.py` to use LangGraph.
  2. Swap the in-memory storage for real SQLite.
  3. Decide on the simulator's technical architecture (Docker containers vs API-driven python mockup) and build its foundation.

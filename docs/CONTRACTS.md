# Contracts

**`backend/contracts/` is the integration boundary between all developers.**

## Rule

> Do NOT make developers depend on each other's internal implementation.

### ✅ GOOD

```python
# In orchestrator/pipeline.py
from backend.contracts import LogInvestigationResult
result: LogInvestigationResult = await self.log_investigator.investigate(...)
```

### ❌ BAD

```python
# In orchestrator/pipeline.py
from backend.agents.log_investigator import _internal_var
```

## Model Reference

### Core Flow Models

| Model | Input From | Output To | Key Fields |
|---|---|---|---|
| `Incident` | API trigger | Everywhere | `id`, `service_name`, `state`, `severity`, `timeline` |
| `TelemetryEvent` | Simulator | Investigators | `source`, `event_type`, `value`, `metadata` |
| `LogInvestigationResult` | Log Investigator | Arbiter | `hypothesis`, `evidence`, `confidence`, `suggested_root_cause` |
| `MetricInvestigationResult` | Metric Investigator | Arbiter | `hypothesis`, `evidence`, `confidence`, `suggested_root_cause` |
| `ArbiterResult` | Arbiter | Severity | `root_cause`, `confidence`, `conflict_description` |
| `SeverityResult` | Severity Agent | Orchestrator | `severity`, `blast_radius`, `affected_services` |
| `RemediationRequest` | Orchestrator | Remediation Engine | `action`, `target_service`, `requires_approval` |
| `RemediationResult` | Remediation Engine | Orchestrator | `success`, `message`, `before_state`, `after_state` |
| `VerificationResult` | Verification | Orchestrator | `verified`, `checks_passed`, `checks_total` |
| `IncidentReport` | Reporter | API response | `root_cause`, `impact`, `confidence`, `prevention` |

### Enums

| Enum | Values |
|---|---|
| `IncidentState` | `created`, `detected`, `investigating`, `analyzing`, `severity_determined`, `remediating`, `verifying`, `resolved`, `escalated`, `failed` |
| `PipelineStage` | `detection`, `investigation`, `arbiter`, `severity`, `autonomy`, `remediation`, `verification`, `report` |
| `SeverityLevel` | `P1`, `P2`, `P3`, `P4` |
| `AutonomyLevel` | `assist`, `semi`, `autonomous` |

### Timeline Event

```json
{
  "id": "uuid",
  "timestamp": "2026-01-01T00:00:00Z",
  "stage": "investigation",
  "status": "completed",
  "message": "Investigation complete — confidence 85%",
  "metadata": {}
}
```

Every pipeline stage MUST append a TimelineEvent.

## How to Extend

To add a new model:

1. Add it to `backend/contracts/models.py`
2. Export it from `backend/contracts/__init__.py`
3. Import it as `from backend.contracts import NewModel`
4. Do NOT add implementation-specific fields to shared models

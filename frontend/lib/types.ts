// Shared frontend types — mirror backend/contracts/models.py exactly.
// This is the ONLY place backend-shaped types are defined; do not redeclare
// partial versions of these interfaces in page components.

export type SeverityLevel = "P1" | "P2" | "P3" | "P4";

export type AutonomyLevel = "assist" | "semi" | "autonomous";

export type IncidentState =
  | "created"
  | "detected"
  | "investigating"
  | "analyzing"
  | "severity_determined"
  | "remediating"
  | "verifying"
  | "resolved"
  | "escalated"
  | "rejected"
  | "failed";

export type PipelineStage =
  | "detection"
  | "investigation"
  | "arbiter"
  | "severity"
  | "autonomy"
  | "remediation"
  | "verification"
  | "report";

export const PIPELINE_STAGE_ORDER: PipelineStage[] = [
  "detection",
  "investigation",
  "arbiter",
  "severity",
  "autonomy",
  "remediation",
  "verification",
  "report",
];

export interface TimelineEvent {
  id: string;
  timestamp: string;
  stage: PipelineStage;
  status: string; // "started" | "completed" | "failed"
  message: string;
  metadata: Record<string, unknown>;
}

export interface TelemetryEvent {
  id: string;
  timestamp: string;
  source: string;
  event_type: string;
  value: unknown;
  metadata: Record<string, unknown>;
}

export interface LogInvestigationResult {
  hypothesis: string;
  evidence: string[];
  confidence: number;
  suggested_root_cause: string;
}

export interface MetricInvestigationResult {
  hypothesis: string;
  evidence: string[];
  confidence: number;
  suggested_root_cause: string;
  metrics_summary: Record<string, unknown>;
}

export interface ArbiterResult {
  merged_hypothesis: string;
  root_cause: string;
  confidence: number;
  log_hypothesis_agrees: boolean;
  metric_hypothesis_agrees: boolean;
  conflict_description: string | null;
  evidence: string[];
  contributing_factors: string[];
  retry_count: number;
}

export interface SeverityResult {
  severity: SeverityLevel;
  blast_radius: number;
  affected_services: string[];
  justification: string;
}

export interface RemediationRequest {
  action: string;
  description: string;
  target_service: string;
  requires_approval: boolean;
}

export interface RemediationResult {
  action: string;
  success: boolean;
  message: string;
  before_state: Record<string, unknown>;
  after_state: Record<string, unknown>;
}

export interface VerificationResult {
  verified: boolean;
  checks_passed: number;
  checks_total: number;
  message: string;
  recovered_metrics: Record<string, unknown>;
}

export interface IncidentReport {
  root_cause: string;
  impact: string;
  remediation_action: string;
  confidence: number;
  prevention: string;
  result_metrics: Record<string, { before: unknown; after: unknown }>;
  timeline_summary: string[];
}

/** Shape returned by GET /incidents (list endpoint) */
export interface IncidentSummary {
  id: string;
  service_name: string;
  state: IncidentState;
  severity: SeverityLevel | null;
  current_stage: PipelineStage | null;
  created_at: string;
}

/** Full shape returned by GET /incidents/{id} */
export interface Incident {
  id: string;
  created_at: string;
  updated_at: string;
  target_url: string | null;
  service_name: string;
  service_url: string | null;
  state: IncidentState;
  severity: SeverityLevel | null;
  autonomy_level: AutonomyLevel | null;
  current_stage: PipelineStage | null;
  signals: TelemetryEvent[];
  log_result: LogInvestigationResult | null;
  metric_result: MetricInvestigationResult | null;
  arbiter_result: ArbiterResult | null;
  severity_result: SeverityResult | null;
  remediation_request: RemediationRequest | null;
  remediation_result: RemediationResult | null;
  verification_result: VerificationResult | null;
  report: IncidentReport | null;
  timeline: TimelineEvent[];
}

export interface TriggerResponse {
  incident_id: string;
  status: string;
  message: string;
}

export interface ApprovalStatus {
  incident_id: string;
  has_pending_approval: boolean;
}

export interface ApprovalResponse {
  incident_id: string;
  status: string;
  message: string;
}

export interface KnowledgeBaseResult {
  id: number;
  incident_id: string | null;
  service: string;
  root_cause: string;
  description: string;
  resolved_via: string;
  created_at: string;
  similarity: number;
}

/** Shape returned by GET /services/health — DockerController.check_health() */
export interface ServiceHealth {
  service: string;
  running: boolean;
  health: string; // "healthy" | "unhealthy" | "starting" | "not_found" | "none" | "unknown"
  version: string;
  container_id?: string;
  image?: string;
}

/**
 * Trigger scenarios supported by POST /incidents/trigger
 * (backend/api/app.py TriggerRequest docstring: bad_deployment | database_failure
 * | dependency_outage | resource_exhaustion). `service` is the default target
 * matching backend/simulator/scenarios.py's own function defaults — all three
 * real-environment scenarios default to "payment-service" (the container real
 * remediation acts on); resource_exhaustion has no real backend target yet
 * and stays a placeholder name.
 */
export const SCENARIOS: { id: string; label: string; service: string; description: string }[] = [
  {
    id: "bad_deployment",
    label: "Bad Deployment",
    service: "payment-service",
    description: "New version causes errors and a latency spike",
  },
  {
    id: "database_failure",
    label: "Database Failure",
    service: "payment-service",
    description: "Connection pool exhaustion, queries time out",
  },
  {
    id: "dependency_outage",
    label: "Dependency Outage",
    service: "payment-service",
    description: "An external dependency stops responding",
  },
  {
    id: "resource_exhaustion",
    label: "Resource Exhaustion",
    service: "order-service",
    description: "CPU/memory leak causes gradual degradation",
  },
];

// Real services from docker-compose.yml — used for the health grid.
// (toxiproxy is infra, not an application service, so it's excluded here.)
export const KNOWN_SERVICES = [
  "payment-service",
  "rpc-service-primary",
  "rpc-service-secondary",
  "db-service",
] as const;

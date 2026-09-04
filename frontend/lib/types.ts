/* ------------------------------------------------------------------ *
 *  System Bachao — mock domain types (frontend demo data)
 * ------------------------------------------------------------------ */

export type Health = "healthy" | "degraded" | "down";
export type NodeRole = "edge" | "service" | "data" | "external";

export interface GraphNode {
  key: string;
  /** display name, e.g. payment-service */
  label: string;
  role: NodeRole;
  /** svg coordinates */
  x: number;
  y: number;
  /** upstream services this node calls */
  calls: string[];
  /** one-liner for the inspector */
  blurb: string;
}

export interface EdgeSpec {
  from: string;
  to: string;
  /** dashed ring / external dependency */
  external?: boolean;
}

export type PipelineStageKey =
  | "detect"
  | "triage"
  | "investigate"
  | "rca"
  | "remediate"
  | "verify";

export type EnginePhase =
  | "standby" // no incident
  | "detect"
  | "triage"
  | "investigate"
  | "rca"
  | "awaiting_approval" // remediation proposal shown
  | "executing" // remediation running
  | "escalated" // human rejected, waiting for manual confirm
  | "verify"
  | "resolved";

export type LogTone = "info" | "warn" | "crit" | "ok" | "system";

export interface LogEntry {
  id: number;
  t: string; // clock label, e.g. 07:42:09
  stage: string;
  msg: string;
  tone: LogTone;
}

export interface DetectSignal {
  source: string;
  metric: string;
  value: string;
  tone: "warn" | "crit";
  note: string;
}

export interface Finding {
  agent: "LOG-INVESTIGATOR" | "METRIC-INVESTIGATOR" | "ARBITER";
  headline: string;
  detail: string;
  evidence: string[];
  confidence: number; // 0..1 snapshot after this finding
}

export interface VerifyCheck {
  label: string;
  detail: string;
}

export interface ReportMetric {
  label: string;
  before: string;
  after: string;
  ok?: boolean;
}

export interface GraphBeat {
  when: PipelineStageKey;
  /** health changes to apply */
  set?: Record<string, Health>;
  /** alert pulses to animate, pairs [from, to] */
  pulses?: Array<[string, string]>;
}

export interface RcaInfo {
  headline: string;
  narrative: string;
  evidence: string[];
  confidencePct: number;
}

export interface RemediationProposal {
  action: string;
  target: string;
  description: string;
  risk: "LOW" | "MEDIUM" | "HIGH";
  rollbackInfo: string;
  steps: string[];
  requiresApproval: boolean;
}

export interface Scenario {
  key: string;
  label: string;
  tagline: string;
  chip: { text: string; dot: string }; // tailwind color tokens
  featured?: boolean;
  severity: "P1" | "P2";
  autonomy: "AUTONOMOUS" | "SEMI-AUTONOMOUS" | "ASSIST";
  /** which node first goes down */
  failedNode: string;
  /** node the RCA points at */
  rcNode: string;
  headline: string;
  triageSummary: string;
  detectSummary: string;
  detectSignals: DetectSignal[];
  spread: GraphBeat[];
  findings: Finding[];
  rca: RcaInfo;
  remediation: RemediationProposal;
  verifyChecks: VerifyCheck[];
  report: {
    rootCause: string;
    impact: string;
    actionTaken: string;
    prevention: string[];
    metrics: ReportMetric[];
  };
}

export interface IncidentSummary {
  id: string;
  scenarioKey: string;
  severity: string;
  autonomy: string;
  serviceName: string;
  headline: string;
  startedAt: string; // ISO
  resolvedAt?: string; // ISO
  durationSec?: number;
  status: "open" | "resolved";
}

/** What the engine hands the rest of the app while running */
export interface SimulationState {
  phase: EnginePhase;
  incident: IncidentSummary | null;
  stageIdx: number; // pipeline step currently active (-1 idle)
  stagesDone: boolean[];
  nodeHealth: Record<string, Health>;
  /** pairs currently emitting alert pulses */
  pulses: Array<[string, string]>;
  rcNode: string | null;
  rcShown: boolean;
  rca: RcaInfo | null;
  signals: DetectSignal[];
  signalsShown: number;
  findings: Finding[];
  findingsShown: number;
  log: LogEntry[];
  confidence: number | null;
  proposal: RemediationProposal | null;
  proposalStatus: "pending" | "approved" | "rejected" | "executing" | "done";
  approvalCountdown: number;
  checks: VerifyCheck[];
  checksShown: number;
  report: PostMortemReport | null;
  elapsedMs: number;
  error?: string | null;
}

export interface PostMortemTimelineItem {
  t: string;
  stage: string;
  msg: string;
  tone: LogTone;
}

export interface PostMortemReport {
  id: string;
  scenarioKey: string;
  severity: string;
  autonomy: string;
  serviceName: string;
  headline: string;
  startedAt: string;
  resolvedAt: string;
  durationSec: number;
  rootCauseNode: string;
  rootCause: string;
  impact: string;
  actionTaken: string;
  risk: string;
  confidencePct: number;
  prevention: string[];
  metrics: ReportMetric[];
  checks: VerifyCheck[];
  timeline: PostMortemTimelineItem[];
}

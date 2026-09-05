"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { Button, Chip, MicroLabel, Panel, StatusDot } from "@/components/ui";
import { IconCheck } from "@/components/icons";
import {
  ApiError,
  approveIncident,
  getApprovalStatus,
  getIncident,
  incidentWebSocketUrl,
  rejectIncident,
} from "@/lib/api";
import {
  PIPELINE_STAGE_ORDER,
  type Incident,
  type PipelineStage,
  type SeverityLevel,
  type TimelineEvent,
} from "@/lib/types";

const TERMINAL_STATES = new Set(["resolved", "failed", "escalated", "rejected"]);

function serviceDisplayName(key: string): string {
  if (!key) return key;
  return key
    .split("-")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function severityTone(sev: SeverityLevel | null): "crit" | "warn" | "info" | "neutral" {
  if (sev === "P1") return "crit";
  if (sev === "P2") return "warn";
  if (sev === "P3" || sev === "P4") return "info";
  return "neutral";
}

function stateTone(state: string): "ok" | "crit" | "warn" | "info" {
  if (state === "resolved") return "ok";
  if (state === "failed" || state === "escalated") return "crit";
  if (state === "rejected") return "warn";
  return "info";
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

/* ── stage stepper — driven entirely by incident.current_stage + timeline ── */

function StageStepper({ incident, timeline }: { incident: Incident; timeline: TimelineEvent[] }) {
  const completedStages = new Set(
    timeline.filter((e) => e.status === "completed").map((e) => e.stage)
  );
  const failedStages = new Set(timeline.filter((e) => e.status === "failed").map((e) => e.stage));
  const currentIdx = incident.current_stage ? PIPELINE_STAGE_ORDER.indexOf(incident.current_stage) : -1;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {PIPELINE_STAGE_ORDER.map((stage, i) => {
        const done = completedStages.has(stage);
        const failed = failedStages.has(stage);
        const active = stage === incident.current_stage && !TERMINAL_STATES.has(incident.state);
        return (
          <div key={stage} className="flex items-center gap-2">
            <div
              className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 font-mono text-[10px] tracking-[0.08em] ${
                failed
                  ? "border-[rgba(255,77,103,0.4)] bg-[rgba(255,77,103,0.08)] text-[var(--color-accent-red)]"
                  : done
                    ? "border-[rgba(0,214,163,0.3)] bg-[rgba(0,214,163,0.06)] text-[var(--color-status-healthy)]"
                    : active
                      ? "border-[rgba(54,215,232,0.4)] bg-[rgba(54,215,232,0.08)] text-[var(--color-accent-cyan)]"
                      : "border-[var(--color-border-subtle)] text-[var(--color-text-faint)]"
              }`}
            >
              {done && <IconCheck className="h-2.5 w-2.5" />}
              {stage.toUpperCase()}
            </div>
            {i < PIPELINE_STAGE_ORDER.length - 1 && (
              <span className="text-[var(--color-text-faint)]">→</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function IncidentPage() {
  const params = useParams();
  const id = (params.id as string) ?? "";

  const [incident, setIncident] = useState<Incident | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [connected, setConnected] = useState(false);

  const [pendingApproval, setPendingApproval] = useState(false);
  const [approvalSubmitting, setApprovalSubmitting] = useState<"approve" | "reject" | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const incidentStateRef = useRef<string | null>(null);

  const refreshIncident = useCallback(async () => {
    try {
      const data = await getIncident(id);
      setIncident(data);
      // Defensive de-dupe: the backend replays this same list to every new
      // WebSocket connection, and in dev, React's Strict Mode briefly opens
      // two connections per mount — dedupe by id so a stray duplicate never
      // reaches state (see the WS handler's own stale-socket guard below).
      const seen = new Set<string>();
      setTimeline(data.timeline.filter((e) => (seen.has(e.id) ? false : seen.add(e.id))));
      incidentStateRef.current = data.state;
      setFetchError(null);
    } catch (e) {
      setFetchError(e instanceof ApiError ? e.message : "Failed to load incident");
    }
  }, [id]);

  const refreshApproval = useCallback(async () => {
    try {
      const status = await getApprovalStatus(id);
      setPendingApproval(status.has_pending_approval);
    } catch {
      // Non-critical — approval banner just won't show if this fails.
    }
  }, [id]);

  useEffect(() => {
    if (!id) return;
    refreshIncident();
    refreshApproval();
  }, [id, refreshIncident, refreshApproval]);

  // Fallback polling safety net — WebSocket is the primary update path, but
  // this keeps state fresh if a socket message is ever missed, without
  // fabricating any progress on its own.
  useEffect(() => {
    if (!id) return;
    const interval = setInterval(() => {
      if (incidentStateRef.current && TERMINAL_STATES.has(incidentStateRef.current)) return;
      refreshIncident();
      refreshApproval();
    }, 10000);
    return () => clearInterval(interval);
  }, [id, refreshIncident, refreshApproval]);

  // WebSocket for live timeline, with reconnect-on-disconnect.
  useEffect(() => {
    if (!id) return;
    mountedRef.current = true;

    const connect = () => {
      if (wsRef.current) return; // avoid duplicate subscriptions
      const ws = new WebSocket(incidentWebSocketUrl(id));
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setConnected(true);
      };

      ws.onmessage = (event) => {
        if (!mountedRef.current) return;
        // Ignore messages from a socket that's no longer the active one —
        // guards against a stale connection (e.g. React Strict Mode's
        // double-mount in dev) still delivering its own replay of the
        // incident's existing timeline after a newer socket has taken over.
        if (wsRef.current !== ws) return;
        let data: TimelineEvent | { type: string };
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        if ("type" in data && data.type === "ping") return; // keepalive

        const evt = data as TimelineEvent;
        setTimeline((prev) => (prev.some((e) => e.id === evt.id) ? prev : [...prev, evt]));
        refreshIncident();
        refreshApproval();
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (!mountedRef.current) return;
        setConnected(false);
        if (incidentStateRef.current && TERMINAL_STATES.has(incidentStateRef.current)) return;
        reconnectTimerRef.current = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [id, refreshIncident, refreshApproval]);

  const submitApproval = async (approved: boolean) => {
    setApprovalSubmitting(approved ? "approve" : "reject");
    setApprovalError(null);
    try {
      if (approved) {
        await approveIncident(id);
      } else {
        await rejectIncident(id);
      }
      await Promise.all([refreshApproval(), refreshIncident()]);
    } catch (e) {
      setApprovalError(e instanceof ApiError ? e.message : "Failed to submit decision");
    } finally {
      setApprovalSubmitting(null);
    }
  };

  if (fetchError && !incident) {
    return (
      <div className="mx-auto max-w-[720px] px-4 py-16 text-center">
        <p className="text-[15px] font-medium text-[var(--color-accent-red)]">Couldn&apos;t load this incident.</p>
        <p className="mt-2 text-[13px] text-[var(--color-text-muted)]">{fetchError}</p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <Button variant="secondary" onClick={refreshIncident}>
            Retry
          </Button>
          <Link href="/dashboard">
            <Button variant="ghost">Back to Dashboard</Button>
          </Link>
        </div>
      </div>
    );
  }

  if (!incident) {
    return (
      <div className="grid min-h-screen place-items-center">
        <div className="text-center space-y-4">
          <div className="mx-auto h-8 w-8 rounded-full border-2 border-[var(--color-border-default)] border-t-[var(--color-accent-cyan)] animate-spin" />
          <p className="font-mono text-[11px] tracking-[0.14em] text-[var(--color-text-muted)]">
            loading incident
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      {/* ── header ── */}
      <header className="glass-nav sticky top-0 z-40">
        <div className="mx-auto flex max-w-[1280px] items-center gap-4 px-4 py-2.5">
          <Link
            href="/dashboard"
            className="label-micro shrink-0 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text-primary)]"
          >
            ← DASHBOARD
          </Link>
          <div className="mx-1 hidden h-5 w-px bg-[var(--color-border-subtle)] sm:block" />
          <span className="text-[14px] font-semibold tracking-[0.06em] text-[var(--color-text-primary)]">
            {serviceDisplayName(incident.service_name)}
          </span>
          <Chip tone={stateTone(incident.state)}>{incident.state}</Chip>
          {incident.severity && <Chip tone={severityTone(incident.severity)}>{incident.severity}</Chip>}
          <span className="ml-auto flex items-center gap-1.5">
            <StatusDot state={connected ? "healthy" : "neutral"} size="sm" pulse={connected} />
            <span className="font-mono text-[10px] tracking-[0.1em] text-[var(--color-text-muted)]">
              {connected ? "LIVE" : "RECONNECTING…"}
            </span>
          </span>
          <span className="hidden font-mono text-[11px] tracking-[0.06em] text-[var(--color-text-faint)] sm:inline">
            {incident.id}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-[1280px] px-4 py-6 space-y-6">
        {/* ── approval gate — real endpoints only ── */}
        {pendingApproval && incident.remediation_request && (
          <Panel className="border-[rgba(245,184,75,0.3)]" title="Approval Required">
            <p className="text-[13px] text-[var(--color-text-secondary)]">
              Remediation <strong className="text-[var(--color-text-primary)]">{incident.remediation_request.action}</strong> on{" "}
              <strong className="text-[var(--color-text-primary)]">{incident.remediation_request.target_service}</strong> needs
              human approval before it executes ({incident.severity} — semi-autonomous tier).
            </p>
            {incident.remediation_request.description && (
              <p className="mt-2 text-[13px] text-[var(--color-text-muted)]">{incident.remediation_request.description}</p>
            )}
            <div className="mt-4 flex items-center gap-3">
              <Button
                variant="primary"
                onClick={() => submitApproval(true)}
                disabled={approvalSubmitting !== null}
              >
                {approvalSubmitting === "approve" ? "Approving…" : "Approve"}
              </Button>
              <Button
                variant="danger"
                onClick={() => submitApproval(false)}
                disabled={approvalSubmitting !== null}
              >
                {approvalSubmitting === "reject" ? "Rejecting…" : "Reject"}
              </Button>
            </div>
            {approvalError && <p className="mt-2 text-[13px] text-[var(--color-accent-red)]">{approvalError}</p>}
          </Panel>
        )}

        {/* ── stage progress ── */}
        <Panel title="Pipeline Progress">
          <StageStepper incident={incident} timeline={timeline} />
        </Panel>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* ── summary ── */}
          <Panel title="Incident Summary">
            <dl className="grid grid-cols-2 gap-y-3 text-[13px]">
              <dt className="text-[var(--color-text-muted)]">Service</dt>
              <dd className="text-[var(--color-text-primary)]">{serviceDisplayName(incident.service_name)}</dd>
              <dt className="text-[var(--color-text-muted)]">State</dt>
              <dd>
                <Chip tone={stateTone(incident.state)}>{incident.state}</Chip>
              </dd>
              <dt className="text-[var(--color-text-muted)]">Severity</dt>
              <dd className="text-[var(--color-text-primary)]">{incident.severity ?? "—"}</dd>
              <dt className="text-[var(--color-text-muted)]">Autonomy</dt>
              <dd className="text-[var(--color-text-primary)]">{incident.autonomy_level ?? "—"}</dd>
              <dt className="text-[var(--color-text-muted)]">Current Stage</dt>
              <dd className="text-[var(--color-text-primary)]">{incident.current_stage ?? "—"}</dd>
              <dt className="text-[var(--color-text-muted)]">Signals</dt>
              <dd className="text-[var(--color-text-primary)]">{incident.signals.length}</dd>
              <dt className="text-[var(--color-text-muted)]">Created</dt>
              <dd className="text-[var(--color-text-primary)]">{new Date(incident.created_at).toLocaleString()}</dd>
            </dl>
          </Panel>

          {/* ── timeline ── */}
          <Panel title="Timeline">
            <div className="max-h-[280px] space-y-1.5 overflow-y-auto">
              {timeline.length === 0 ? (
                <p className="text-[13px] text-[var(--color-text-muted)]">Waiting for events…</p>
              ) : (
                timeline.map((event) => (
                  <div
                    key={event.id}
                    className={`rounded-md px-3 py-2 text-[13px] ${
                      event.status === "failed"
                        ? "bg-[rgba(255,77,103,0.06)]"
                        : event.status === "started"
                          ? "bg-[rgba(245,184,75,0.06)]"
                          : "bg-[var(--color-bg-surface)]"
                    }`}
                  >
                    <span className="font-medium text-[var(--color-text-primary)]">{event.stage}</span>
                    <span className="text-[var(--color-text-secondary)]"> — {event.message}</span>
                    <div className="mt-0.5 font-mono text-[11px] text-[var(--color-text-faint)]">
                      {formatTime(event.timestamp)} · {event.status}
                    </div>
                  </div>
                ))
              )}
            </div>
          </Panel>
        </div>

        {/* ── severity & blast radius ── */}
        {incident.severity_result && (
          <Panel title="Severity & Blast Radius">
            <p className="text-[13px] text-[var(--color-text-secondary)]">{incident.severity_result.justification}</p>
            <p className="mt-2 text-[13px] text-[var(--color-text-primary)]">
              <strong>{incident.severity_result.blast_radius}</strong> service(s) affected
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {incident.severity_result.affected_services.map((svc) => (
                <Chip key={svc} tone={svc === incident.service_name ? "crit" : "warn"}>
                  {serviceDisplayName(svc)}
                </Chip>
              ))}
            </div>
          </Panel>
        )}

        {/* ── investigation ── */}
        {(incident.log_result || incident.metric_result || incident.arbiter_result) && (
          <Panel title="Investigation">
            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
              {incident.log_result && (
                <div>
                  <MicroLabel>Log Investigator</MicroLabel>
                  <p className="mt-1.5 text-[13px] text-[var(--color-text-primary)]">{incident.log_result.hypothesis}</p>
                  <p className="mt-1 text-[12px] text-[var(--color-text-muted)]">
                    root cause: {incident.log_result.suggested_root_cause}
                  </p>
                  <div className="mt-2 h-1.5 w-full rounded-full bg-[var(--color-border-subtle)]">
                    <div
                      className="h-1.5 rounded-full bg-[var(--color-accent-cyan)]"
                      style={{ width: `${Math.round(incident.log_result.confidence * 100)}%` }}
                    />
                  </div>
                  <p className="mt-1 text-[11px] text-[var(--color-text-faint)]">
                    {(incident.log_result.confidence * 100).toFixed(0)}% confidence
                  </p>
                </div>
              )}
              {incident.metric_result && (
                <div>
                  <MicroLabel>Metric Investigator</MicroLabel>
                  <p className="mt-1.5 text-[13px] text-[var(--color-text-primary)]">{incident.metric_result.hypothesis}</p>
                  <p className="mt-1 text-[12px] text-[var(--color-text-muted)]">
                    root cause: {incident.metric_result.suggested_root_cause}
                  </p>
                  <div className="mt-2 h-1.5 w-full rounded-full bg-[var(--color-border-subtle)]">
                    <div
                      className="h-1.5 rounded-full bg-[var(--color-accent-purple)]"
                      style={{ width: `${Math.round(incident.metric_result.confidence * 100)}%` }}
                    />
                  </div>
                  <p className="mt-1 text-[11px] text-[var(--color-text-faint)]">
                    {(incident.metric_result.confidence * 100).toFixed(0)}% confidence
                  </p>
                </div>
              )}
            </div>
            {incident.arbiter_result && (
              <div className="mt-5 border-t border-[var(--color-border-subtle)] pt-4">
                <MicroLabel>Arbiter — Merged Verdict</MicroLabel>
                <p className="mt-1.5 text-[13px] text-[var(--color-text-primary)]">
                  {incident.arbiter_result.merged_hypothesis}
                </p>
                <p className="mt-1 text-[12px] text-[var(--color-text-muted)]">
                  root cause: <strong className="text-[var(--color-text-primary)]">{incident.arbiter_result.root_cause}</strong> ·{" "}
                  {(incident.arbiter_result.confidence * 100).toFixed(0)}% confidence
                </p>
                {incident.arbiter_result.conflict_description && (
                  <div className="mt-2 rounded-md border border-[rgba(245,184,75,0.3)] bg-[rgba(245,184,75,0.06)] px-3 py-2 text-[13px] text-[var(--color-accent-amber)]">
                    Investigators disagreed: {incident.arbiter_result.conflict_description}
                  </div>
                )}
              </div>
            )}
          </Panel>
        )}

        {/* ── remediation ── */}
        {incident.remediation_request && (
          <Panel title="Remediation">
            <p className="text-[13px] text-[var(--color-text-primary)]">
              <strong>{incident.remediation_request.action}</strong> on {incident.remediation_request.target_service}
            </p>
            <p className="mt-1 text-[13px] text-[var(--color-text-muted)]">{incident.remediation_request.description}</p>
            {incident.remediation_result && (
              <div
                className={`mt-3 rounded-md px-3 py-2 text-[13px] ${
                  incident.remediation_result.success
                    ? "bg-[rgba(0,214,163,0.06)] text-[var(--color-status-healthy)]"
                    : "bg-[rgba(255,77,103,0.06)] text-[var(--color-accent-red)]"
                }`}
              >
                {incident.remediation_result.success ? "✓" : "✕"} {incident.remediation_result.message}
              </div>
            )}
          </Panel>
        )}

        {/* ── verification ── */}
        {incident.verification_result && (
          <Panel title="Verification">
            <p className="text-[13px] text-[var(--color-text-primary)]">
              {incident.verification_result.verified ? "✓ Verified" : "✕ Not verified"} —{" "}
              {incident.verification_result.checks_passed}/{incident.verification_result.checks_total} checks passed
            </p>
            <p className="mt-1 text-[13px] text-[var(--color-text-muted)]">{incident.verification_result.message}</p>
          </Panel>
        )}

        {/* ── report ── */}
        {incident.report && (
          <Panel title="Incident Report">
            <dl className="grid grid-cols-1 gap-y-3 text-[13px] sm:grid-cols-2">
              <dt className="text-[var(--color-text-muted)]">Root Cause</dt>
              <dd className="text-[var(--color-text-primary)]">{incident.report.root_cause}</dd>
              <dt className="text-[var(--color-text-muted)]">Impact</dt>
              <dd className="text-[var(--color-text-primary)]">{incident.report.impact}</dd>
              <dt className="text-[var(--color-text-muted)]">Remediation</dt>
              <dd className="text-[var(--color-text-primary)]">{incident.report.remediation_action}</dd>
              <dt className="text-[var(--color-text-muted)]">Confidence</dt>
              <dd className="text-[var(--color-text-primary)]">{(incident.report.confidence * 100).toFixed(0)}%</dd>
              <dt className="text-[var(--color-text-muted)]">Prevention</dt>
              <dd className="text-[var(--color-text-primary)] sm:col-span-1">{incident.report.prevention}</dd>
            </dl>
          </Panel>
        )}
      </main>
    </div>
  );
}

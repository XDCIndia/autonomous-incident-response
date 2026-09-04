"use client";

import { useEffect, useRef, useState } from "react";
import type { EnginePhase, SimulationState } from "@/lib/types";
import { Button, Chip, MicroLabel, Panel } from "./ui";
import { IconCheck } from "./icons";

const PHASE_CHIP: Record<EnginePhase, { label: string; tone: "ok" | "warn" | "crit" | "info" | "system" | "neutral" }> = {
  standby: { label: "STANDBY", tone: "neutral" },
  detect: { label: "ANOMALY OPEN", tone: "crit" },
  triage: { label: "TRIAGING", tone: "warn" },
  investigate: { label: "INVESTIGATING", tone: "info" },
  rca: { label: "ROOT CAUSE", tone: "warn" },
  awaiting_approval: { label: "APPROVAL GATE", tone: "crit" },
  executing: { label: "REMEDIATING", tone: "warn" },
  escalated: { label: "ESCALATED", tone: "crit" },
  verify: { label: "VERIFYING", tone: "ok" },
  resolved: { label: "RESOLVED", tone: "ok" },
};

const AGENT_TONE: Record<string, "ok" | "warn" | "crit" | "info" | "system"> = {
  "LOG-INVESTIGATOR": "info",
  "METRIC-INVESTIGATOR": "system",
  ARBITER: "warn",
};

function ConfidenceBar({ pct, tone = "info" }: { pct: number; tone?: "info" | "ok" | "warn" }) {
  const color =
    tone === "ok" ? "bg-[var(--color-status-healthy)]" : tone === "warn" ? "bg-[var(--color-accent-amber)]" : "bg-[var(--color-accent-cyan)]";
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between font-mono text-[10px] text-[var(--color-text-muted)]">
        <span className="tracking-[0.14em]">CONFIDENCE</span>
        <span className={tone === "ok" ? "text-[var(--color-status-healthy)]" : tone === "warn" ? "text-[var(--color-accent-amber)]" : "text-[var(--color-accent-cyan)]"}>
          {Math.round(pct)}%
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-hover)]">
        <div
          className={`h-full rounded-full ${color} transition-all duration-1000 ease-out`}
          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
        />
      </div>
    </div>
  );
}

function CountdownBar({ seconds, total }: { seconds: number; total: number }) {
  const pct = Math.max(0, Math.min(100, (seconds / total) * 100));
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-1 overflow-hidden rounded-full bg-[var(--color-bg-hover)]">
        <div
          className="h-full rounded-full bg-[var(--color-accent-cyan)] transition-all duration-1000 ease-linear"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono text-[11px] font-medium tabular-nums text-[var(--color-accent-cyan)]">
        {seconds}s
      </span>
    </div>
  );
}

function SectionHead({ children, tone = "neutral" }: { children: string; tone?: "neutral" | "crit" | "ok" | "warn" }) {
  const color =
    tone === "crit"
      ? "border-[rgba(255,77,103,0.2)] text-[var(--color-accent-red)]"
      : tone === "ok"
        ? "border-[rgba(0,214,163,0.2)] text-[var(--color-status-healthy)]"
        : tone === "warn"
          ? "border-[rgba(245,184,75,0.2)] text-[var(--color-accent-amber)]"
          : "border-[var(--color-border-subtle)] text-[var(--color-text-secondary)]";
  return (
    <div className={`mb-2.5 border-b pb-2 ${color}`}>
      <span className="label-micro">{children}</span>
    </div>
  );
}

export function AiConsole({
  sim,
  onApprove,
  onReject,
  onConfirmManual,
}: {
  sim: SimulationState;
  onApprove: () => void;
  onReject: () => void;
  onConfirmManual: () => void;
}) {
  const { phase } = sim;
  const chip = PHASE_CHIP[phase];

  return (
    <Panel
      title="AI investigation console"
      right={<Chip tone={chip.tone} pulse={phase !== "standby" && phase !== "resolved"}>{chip.label}</Chip>}
      className="flex min-h-0 flex-1 flex-col"
      bodyClassName="min-h-0 flex-1 overflow-y-auto p-4"
      flush
    >
      <ConsoleBody sim={sim} onApprove={onApprove} onReject={onReject} onConfirmManual={onConfirmManual} />
    </Panel>
  );
}

function ConsoleBody({
  sim,
  onApprove,
  onReject,
  onConfirmManual,
}: {
  sim: SimulationState;
  onApprove: () => void;
  onReject: () => void;
  onConfirmManual: () => void;
}) {
  const { phase } = sim;
  const activeAffected = Object.entries(sim.nodeHealth)
    .filter(([, h]) => h !== "healthy")
    .map(([k, h]) => ({ key: k, h }));

  if (phase === "standby") return <Standby />;
  if (phase === "detect") return <DetectView sim={sim} />;
  if (phase === "triage") return <TriageView sim={sim} affected={activeAffected} />;
  if (phase === "investigate" || phase === "rca") return <InvestigateView sim={sim} />;
  if (phase === "awaiting_approval" || phase === "executing" || phase === "escalated")
    return (
      <RemediationView sim={sim} onApprove={onApprove} onReject={onReject} onConfirmManual={onConfirmManual} />
    );
  if (phase === "verify") return <VerifyView sim={sim} />;
  return <ResolvedView sim={sim} />;
}

/* ---------------- standby ---------------- */

function Standby() {
  const rows = [
    "signal intake — 8/8 services reporting",
    "anomaly scanner — rule-based, armed",
    "log investigator — correlating agent",
    "metric investigator — regression agent",
    "arbiter — conflict resolution",
    "remediation engine — fixed action set",
  ];
  return (
    <div className="space-y-4">
      <div>
        <SectionHead>Neural core — standby</SectionHead>
        <p className="text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
          All telemetry is nominal. System Bachao watches the service graph and responds
          autonomously the moment an anomaly appears.
        </p>
      </div>
      <div className="space-y-2">
        {rows.map((r) => (
          <div key={r} className="flex items-center gap-2.5 font-mono text-[11px] text-[var(--color-text-muted)]">
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-status-healthy)]" />
            <span>{r}</span>
          </div>
        ))}
      </div>
      <div className="border-t border-[var(--color-border-subtle)] pt-3">
        <p className="font-mono text-[11px] text-[var(--color-text-faint)]">
          Press <span className="text-[var(--color-accent-red)]">SIMULATE FAILURE</span> to inject a fault and
          watch the full response pipeline
          <span className="cursor-block" />
        </p>
      </div>
    </div>
  );
}

/* ---------------- detect ---------------- */

function DetectView({ sim }: { sim: SimulationState }) {
  const signals = sim.signals.slice(0, sim.signalsShown);
  return (
    <div className="space-y-3">
      <SectionHead tone="crit">Raw signal intake</SectionHead>
      {signals.map((s, i) => (
        <div key={i} className="anim-rise">
          <div className={`font-mono text-[11px] leading-snug ${s.tone === "crit" ? "text-[var(--color-accent-red)]" : "text-[var(--color-accent-amber)]"}`}>
            <span className="text-[var(--color-text-faint)]">[{s.source}]</span> {s.metric}{" "}
            <span className={s.tone === "crit" ? "text-[var(--color-accent-red)]" : "text-[var(--color-accent-amber)]"}>{s.value}</span>
          </div>
          <div className="font-mono text-[10px] text-[var(--color-text-faint)]">{s.note}</div>
        </div>
      ))}
      {sim.signalsShown >= sim.signals.length && (
        <div className="anim-rise border-t border-[var(--color-border-subtle)] pt-2.5 font-mono text-[11px] text-[var(--color-accent-cyan)]">
          detector engaged — opening {sim.incident?.severity} incident
          <span className="cursor-block" />
        </div>
      )}
    </div>
  );
}

/* ---------------- triage ---------------- */

function TriageView({ sim, affected }: { sim: SimulationState; affected: Array<{ key: string; h: string }> }) {
  return (
    <div className="space-y-3">
      <SectionHead tone="warn">Incident triaged</SectionHead>
      <div className="flex items-center gap-2.5">
        <span className="font-mono text-2xl font-bold tracking-tight text-[var(--color-accent-red)]">
          {sim.incident?.severity}
        </span>
        <Chip tone="neutral">{sim.incident?.autonomy} MODE</Chip>
      </div>
      <div>
        <MicroLabel>Blast radius</MicroLabel>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {affected.map((a) => (
            <Chip key={a.key} tone={a.h === "down" ? "crit" : "warn"}>{a.key}</Chip>
          ))}
        </div>
      </div>
      <p className="text-[13px] leading-relaxed text-[var(--color-text-primary)]">{sim.incident?.headline}</p>
      {sim.findingsShown === 0 && (
        <p className="font-mono text-[11px] text-[var(--color-text-faint)]">
          mapping dependency graph…<span className="cursor-block" />
        </p>
      )}
    </div>
  );
}

/* ---------------- investigate + rca ---------------- */

function InvestigateView({ sim }: { sim: SimulationState }) {
  const shown = sim.findings.slice(0, sim.findingsShown);
  const nextAgent = sim.findings[sim.findingsShown];
  return (
    <div className="space-y-3">
      <SectionHead tone={phaseOf(sim) === "rca" ? "warn" : "neutral"}>
        {phaseOf(sim) === "rca" ? "Root cause analysis" : "Parallel investigation"}
      </SectionHead>

      {shown.map((f, i) => (
        <div key={i} className="anim-rise space-y-2 border-l-2 border-[var(--color-border-default)] pl-3">
          <div className="flex items-center gap-2">
            <Chip tone={AGENT_TONE[f.agent]} className="!px-1.5 !py-0.5 !text-[10px]">
              {f.agent}
            </Chip>
            <span className="font-mono text-[10px] text-[var(--color-text-faint)]">{Math.round(f.confidence * 100)}% conf</span>
          </div>
          <p className="text-[13px] font-medium leading-snug text-[var(--color-text-primary)]">{f.headline}</p>
          <p className="text-[12px] leading-relaxed text-[var(--color-text-secondary)]">{f.detail}</p>
          <ul className="space-y-1 pl-1">
            {f.evidence.map((e, j) => (
              <li key={j} className="font-mono text-[10px] leading-relaxed text-[var(--color-text-muted)]">
                <span className="text-[var(--color-text-faint)]">▸</span> {e}
              </li>
            ))}
          </ul>
        </div>
      ))}

      {phaseOf(sim) === "investigate" && nextAgent && sim.findingsShown <= sim.findings.length && (
        <p className="font-mono text-[11px] text-[var(--color-text-faint)]">
          awaiting {nextAgent.agent}…<span className="cursor-block" />
        </p>
      )}

      {phaseOf(sim) === "investigate" && !nextAgent && (
        <p className="font-mono text-[11px] text-[var(--color-accent-cyan)]">
          reconciling evidence<span className="cursor-block" />
        </p>
      )}

      {sim.confidence != null && phaseOf(sim) === "investigate" && (
        <ConfidenceBar pct={sim.confidence} />
      )}

      {phaseOf(sim) === "rca" && sim.rcShown && <RcaVerdict sim={sim} />}
    </div>
  );
}

function RcaVerdict({ sim }: { sim: SimulationState }) {
  const rca = sim.rca;
  const node = sim.rcNode;
  const health = node ? sim.nodeHealth[node] : undefined;
  if (!rca || !node) return null;
  return (
    <div className="anim-pop space-y-2.5 rounded-md border border-[rgba(255,77,103,0.25)] bg-[rgba(255,77,103,0.05)] p-3">
      <div className="flex items-center justify-between gap-2">
        <MicroLabel className="text-[var(--color-accent-red)]">Suspected root cause</MicroLabel>
        <Chip tone="crit" pulse>isolated</Chip>
      </div>
      <p className="font-mono text-[13px] font-semibold tracking-wide text-[var(--color-accent-red)]">
        {node}
        {health && health !== "healthy" && (
          <span className="ml-2 text-[10px] tracking-[0.14em] text-[var(--color-accent-red)] opacity-70">
            {health === "down" ? "DOWN" : "DEGRADED"}
          </span>
        )}
      </p>
      <p className="text-[13px] font-medium leading-snug text-[var(--color-text-primary)]">{rca.headline}</p>
      <p className="text-[12px] leading-relaxed text-[var(--color-text-secondary)]">{rca.narrative}</p>
      <ul className="space-y-1">
        {rca.evidence.map((e, i) => (
          <li key={i} className="font-mono text-[10px] leading-relaxed text-[var(--color-text-muted)]">
            <span className="text-[var(--color-accent-red)] opacity-60">▸</span> {e}
          </li>
        ))}
      </ul>
      <ConfidenceBar pct={rca.confidencePct} tone="warn" />
      <p className="font-mono text-[10px] tracking-[0.06em] text-[var(--color-accent-red)] opacity-70">
        highlighted on architecture graph · remediation proposal queued
      </p>
    </div>
  );
}

/* ---------------- remediation ---------------- */

function RemediationView({
  sim,
  onApprove,
  onReject,
  onConfirmManual,
}: {
  sim: SimulationState;
  onApprove: () => void;
  onReject: () => void;
  onConfirmManual: () => void;
}) {
  const p = sim.proposal;
  if (!p) return null;
  const executed = sim.log.filter((l) => l.stage === "REMEDIATE" && l.msg.startsWith("▸")).length;
  const riskTone = p.risk === "HIGH" ? "crit" : p.risk === "MEDIUM" ? "warn" : "ok";

  return (
    <div className="space-y-3">
      <SectionHead tone={sim.phase === "escalated" ? "crit" : "warn"}>
        {sim.phase === "escalated" ? "Escalated to operator" : "Remediation proposal"}
      </SectionHead>

      <div className="flex items-center gap-2">
        <span className="font-mono text-[14px] font-semibold tracking-[0.06em] text-[var(--color-text-primary)]">
          {p.action}
        </span>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)]">on</span>
        <span className="font-mono text-[12px] text-[var(--color-text-secondary)]">{p.target}</span>
        <Chip tone={riskTone} className="ml-auto">RISK {p.risk}</Chip>
      </div>

      <p className="text-[12px] leading-relaxed text-[var(--color-text-secondary)]">{p.description}</p>

      <div className="rounded-md border border-[rgba(245,184,75,0.2)] bg-[rgba(245,184,75,0.04)] p-3">
        <MicroLabel className="text-[var(--color-accent-amber)]">Rollback / undo</MicroLabel>
        <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-[var(--color-accent-amber)] opacity-80">
          {p.rollbackInfo}
        </p>
      </div>

      {sim.phase === "awaiting_approval" && p.requiresApproval && (
        <>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <MicroLabel>Approval window</MicroLabel>
            </div>
            <CountdownBar seconds={Math.max(0, sim.approvalCountdown)} total={30} />
          </div>
          <div className="flex gap-2.5">
            <Button variant="primary" size="md" onClick={onApprove} className="flex-1">
              APPROVE
            </Button>
            <Button variant="danger" size="md" onClick={onReject} className="flex-1">
              REJECT
            </Button>
          </div>
          <p className="font-mono text-[10px] leading-relaxed text-[var(--color-text-faint)]">
            {sim.incident?.autonomy} mode — auto-approves when the window closes. Rejecting
            escalates to the on-call operator.
          </p>
        </>
      )}

      {sim.phase === "awaiting_approval" && !p.requiresApproval && (
        <p className="font-mono text-[11px] text-[var(--color-text-muted)]">
          low-risk action — no approval gate required<span className="cursor-block" />
        </p>
      )}

      {sim.phase === "executing" && (
        <>
          <MicroLabel>Applying — step {Math.min(executed + 1, p.steps.length)}/{p.steps.length}</MicroLabel>
          <ol className="mt-2 space-y-2">
            {p.steps.map((s, i) => {
              const done = i < executed;
              const active = i === executed;
              return (
                <li key={i} className={`flex items-start gap-2.5 text-[12px] ${done ? "text-[var(--color-status-healthy)]" : active ? "text-[var(--color-accent-cyan)]" : "text-[var(--color-text-faint)]"}`}>
                  <span className="mt-0.5">
                    {done ? (
                      <IconCheck className="h-3 w-3 text-[var(--color-status-healthy)]" />
                    ) : active ? (
                      <span className="blink block h-2 w-2 rounded-full bg-[var(--color-accent-cyan)]" />
                    ) : (
                      <span className="block h-2 w-2 rounded-full border border-[var(--color-border-emphasis)]" />
                    )}
                  </span>
                  <span className="font-mono leading-snug">{s}</span>
                </li>
              );
            })}
          </ol>
        </>
      )}

      {sim.phase === "escalated" && (
        <>
          <div className="rounded-md border border-[rgba(255,77,103,0.3)] bg-[rgba(255,77,103,0.06)] p-3">
            <p className="text-[12px] leading-relaxed text-[var(--color-accent-red)]">
              Auto-remediation rejected by operator. System Bachao has stepped back — the
              manual runbook is below. Confirm once your team has applied the fix so the AI
              core can resume verification.
            </p>
          </div>
          <ol className="space-y-2">
            {p.steps.map((s, i) => (
              <li key={i} className="flex items-start gap-2.5 font-mono text-[11px] text-[var(--color-text-secondary)]">
                <span className="mt-px text-[var(--color-accent-red)]">{i + 1}.</span>
                <span className="leading-snug">{s}</span>
              </li>
            ))}
          </ol>
          <Button variant="primary" size="md" onClick={onConfirmManual} className="w-full">
            CONFIRM MANUAL FIX — RESUME VERIFICATION
          </Button>
        </>
      )}
    </div>
  );
}

/* ---------------- verify ---------------- */

function VerifyView({ sim }: { sim: SimulationState }) {
  return (
    <div className="space-y-3">
      <SectionHead tone="ok">Verification in progress</SectionHead>
      {sim.checks.slice(0, sim.checksShown).map((c, i) => (
        <div key={i} className="anim-rise flex items-start gap-2.5">
          <span className="mt-0.5 text-[var(--color-status-healthy)]">
            <IconCheck className="h-3.5 w-3.5" />
          </span>
          <div>
            <p className="text-[12px] font-medium text-[var(--color-text-primary)]">{c.label}</p>
            <p className="font-mono text-[10px] text-[var(--color-status-healthy)] opacity-80">{c.detail}</p>
          </div>
        </div>
      ))}
      {sim.checksShown < sim.checks.length && (
        <p className="font-mono text-[11px] text-[var(--color-text-faint)]">
          running check {sim.checksShown + 1}/{sim.checks.length}
          <span className="cursor-block" />
        </p>
      )}
      {sim.checksShown >= sim.checks.length && (
        <div className="anim-pop rounded-md border border-[rgba(0,214,163,0.25)] bg-[rgba(0,214,163,0.05)] p-3 text-[var(--color-status-healthy)]">
          <p className="font-mono text-[11px] font-semibold tracking-[0.12em]">
            REMEDIATION VERIFIED — TELEMETRY NOMINAL
          </p>
        </div>
      )}
    </div>
  );
}

/* ---------------- resolved / post-mortem ---------------- */

function ResolvedView({ sim }: { sim: SimulationState }) {
  const report = sim.report;
  const [showPost, setShowPost] = useState(false);
  const inc = sim.incident;
  const bodyRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, []);
  if (!report || !inc) return null;

  return (
    <div ref={bodyRef} className="space-y-3">
      <SectionHead tone="ok">Recovery complete</SectionHead>
      <div className="grid grid-cols-2 gap-2">
        {[
          ["INCIDENT", inc.id.replace("INC-", "")],
          ["DURATION", `${Math.round((report.durationSec / 60) * 10) / 10}m`],
          ["ACTION", report.scenarioKey === "bad_deployment" ? "ROLLBACK" : "REMEDIATED"],
          ["CONFIDENCE", `${report.confidencePct}%`],
        ].map(([k, v]) => (
          <div key={k} className="rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)] p-2.5">
            <MicroLabel>{k}</MicroLabel>
            <p className="mt-1 truncate font-mono text-[12px] font-semibold text-[var(--color-text-primary)]">{v}</p>
          </div>
        ))}
      </div>

      <div className="space-y-2">
        {report.metrics.map((m) => (
          <div key={m.label} className="flex items-center gap-2 font-mono text-[10px]">
            <span className="w-24 shrink-0 truncate text-[var(--color-text-muted)]">{m.label}</span>
            <span className="text-[var(--color-accent-red)] opacity-80">{m.before}</span>
            <span className="text-[var(--color-text-faint)]">→</span>
            <span className="text-[var(--color-status-healthy)]">{m.after}</span>
            <span className="ml-auto text-[var(--color-status-healthy)] opacity-70">✓</span>
          </div>
        ))}
      </div>

      <div className="border-t border-[var(--color-border-subtle)] pt-3">
        <div className="mb-2 flex items-center justify-between">
          <MicroLabel>Post-incident actions</MicroLabel>
          <span className="font-mono text-[9px] text-[var(--color-text-faint)]">AUTO-GENERATED</span>
        </div>
        <ul className="space-y-1.5">
          {report.prevention.map((p, i) => (
            <li key={i} className="flex items-start gap-2 text-[12px] leading-snug text-[var(--color-text-secondary)]">
              <span className="mt-px text-[var(--color-accent-cyan)]">▸</span>
              {p}
            </li>
          ))}
        </ul>
      </div>

      <button
        onClick={() => setShowPost((v) => !v)}
        className="w-full rounded-md border border-[rgba(54,215,232,0.2)] bg-[rgba(54,215,232,0.04)] py-2 font-mono text-[11px] tracking-[0.12em] text-[var(--color-accent-cyan)] transition-all duration-200 hover:bg-[rgba(54,215,232,0.08)]"
      >
        {showPost ? "HIDE" : "SHOW"} FULL INCIDENT TIMELINE
      </button>
      {showPost && (
        <div className="max-h-56 space-y-1.5 overflow-y-auto rounded-md border border-[var(--color-border-subtle)] p-2.5">
          {report.timeline.map((e, i) => (
            <div key={i} className="flex gap-2 font-mono text-[10px] leading-relaxed">
              <span className="shrink-0 text-[var(--color-text-faint)]">{e.t}</span>
              <span className="w-16 shrink-0 text-[var(--color-accent-cyan)] opacity-70">{e.stage}</span>
              <span className="text-[var(--color-text-secondary)]">{e.msg}</span>
            </div>
          ))}
        </div>
      )}
      <p className="font-mono text-[10px] leading-relaxed text-[var(--color-text-faint)]">
        Full report: <a href={`/incidents/${inc.id}`} className="text-[var(--color-accent-cyan)] underline decoration-[rgba(54,215,232,0.3)] underline-offset-2 hover:text-[var(--color-accent-teal)]">/incidents/{inc.id}</a>
      </p>
    </div>
  );
}

function phaseOf(sim: SimulationState): EnginePhase {
  return sim.phase;
}

"use client";

import { useEffect, useRef } from "react";
import { NODES } from "@/lib/scenarios";
import type { LogEntry, SimulationState } from "@/lib/types";
import { Chip, MicroLabel, Panel, StatusDot, toneText } from "./ui";

function fmtClock(iso: string): string {
  const d = new Date(iso);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

function fmtElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function CommandPanel({ sim }: { sim: SimulationState }) {
  const inc = sim.incident;
  const running = sim.phase !== "standby" && sim.phase !== "resolved";
  const resolved = sim.phase === "resolved";
  const affected = Object.entries(sim.nodeHealth).filter(([, h]) => h !== "healthy");
  const healthNames = new Set(Object.keys(sim.nodeHealth));

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {/* incident command card */}
      <Panel
        title="Incident command"
        right={
          inc ? (
            <Chip tone={resolved ? "ok" : sim.phase === "escalated" ? "crit" : "crit"} pulse={running}>
              {resolved ? "RESOLVED" : `${inc.severity} · ACTIVE`}
            </Chip>
          ) : (
            <Chip tone="ok" pulse>NOMINAL</Chip>
          )
        }
        className="shrink-0"
      >
        {!inc ? (
          <div className="space-y-3">
            <p className="text-[13px] leading-relaxed text-[var(--color-text-secondary)]">
              No active incident. The command channel is quiet — the AI core is watching the
              service graph and will open an incident automatically.
            </p>
            <div className="flex items-center gap-2 font-mono text-[11px] text-[var(--color-text-muted)]">
              <StatusDot state="healthy" size="sm" pulse />
              <span>8/8 services healthy · telemetry streaming</span>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[13px] font-semibold tracking-[0.08em] text-[var(--color-text-primary)]">
                {inc.id}
              </span>
              <Chip tone={inc.autonomy === "AUTONOMOUS" ? "system" : "neutral"}>
                {inc.autonomy.toLowerCase()}
              </Chip>
            </div>
            <p className="text-[13px] font-medium leading-snug text-[var(--color-text-primary)]">
              {inc.headline}
            </p>
            <div className="grid grid-cols-2 gap-x-3 gap-y-2 border-t border-[var(--color-border-subtle)] pt-3">
              <Meta label="Service" value={inc.serviceName} tone="warn" />
              <Meta
                label="Started"
                value={inc.resolvedAt ? fmtClock(inc.resolvedAt) : fmtClock(inc.startedAt)}
              />
              <Meta label="Elapsed" value={resolved ? formatSec(inc.durationSec ?? 0) : fmtElapsed(sim.elapsedMs)} tone={resolved ? "ok" : "crit"} monoBig={!resolved} />
              <Meta label="Root cause" value={sim.rcNode ?? "—"} tone={sim.rcNode ? "crit" : undefined} />
            </div>
            {affected.length > 0 && (
              <div className="border-t border-[var(--color-border-subtle)] pt-3">
                <MicroLabel className="mb-2 block">Affected services</MicroLabel>
                <div className="flex flex-wrap gap-1.5">
                  {affected.map(([k, h]) => (
                    <Chip key={k} tone={h === "down" ? "crit" : "warn"} pulse={h === "down" && running}>
                      {k}
                    </Chip>
                  ))}
                </div>
              </div>
            )}
            {resolved && (
              <div className="flex items-center gap-2 rounded-md border border-[rgba(0,214,163,0.2)] bg-[rgba(0,214,163,0.05)] p-2.5 font-mono text-[11px] tracking-[0.08em] text-[var(--color-status-healthy)]">
                <StatusDot state="healthy" size="sm" pulse /> SYSTEM RECOVERED — READY FOR NEXT INCIDENT
              </div>
            )}
          </div>
        )}
      </Panel>

      {/* explainable event timeline */}
      <Panel
        title="Incident timeline"
        right={
          <span className="font-mono text-[10px] tracking-[0.12em] text-[var(--color-text-muted)]">
            {sim.log.length > 0 ? `${String(sim.log.length).padStart(2, "0")} EVENTS` : "LIVE FEED"}
          </span>
        }
        className="flex min-h-0 flex-1 flex-col"
        bodyClassName="min-h-0 flex-1 overflow-hidden"
        flush
      >
        <TimelineLog entries={sim.log} />
      </Panel>

      {/* mini service ledger */}
      <div className="hidden shrink-0 grid-cols-4 gap-2 xl:grid">
        {NODES.map((n) => {
          const h = healthNames.has(n.key) ? sim.nodeHealth[n.key] : "healthy";
          const ok = h === "healthy";
          return (
            <div
              key={n.key}
              className={`truncate rounded-md border px-2 py-1.5 font-mono text-[10px] tracking-[0.04em] ${
                h === "down"
                  ? "border-[rgba(255,77,103,0.3)] bg-[rgba(255,77,103,0.06)] text-[var(--color-accent-red)]"
                  : h === "degraded"
                    ? "border-[rgba(245,184,75,0.25)] bg-[rgba(245,184,75,0.05)] text-[var(--color-accent-amber)]"
                    : "border-[var(--color-border-subtle)] text-[var(--color-text-muted)]"
              }`}
              title={n.label}
            >
              <span className={ok ? "text-[var(--color-status-healthy)]" : h === "down" ? "blink text-[var(--color-accent-red)]" : "text-[var(--color-accent-amber)]"}>●</span>{" "}
              {n.label}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Meta({
  label,
  value,
  tone,
  monoBig = false,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "crit";
  monoBig?: boolean;
}) {
  return (
    <div className="min-w-0">
      <MicroLabel className="block">{label}</MicroLabel>
      <p
        className={`truncate font-mono ${monoBig ? "text-[14px] font-semibold tabular-nums" : "text-[11px]"} ${
          tone ? toneText[tone] : "text-[var(--color-text-primary)]"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

function TimelineLog({ entries }: { entries: LogEntry[] }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  return (
    <div ref={ref} className="h-full overflow-y-auto px-4 py-3">
      {entries.length === 0 ? (
        <div className="space-y-2 pt-1">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="flex items-center gap-2 font-mono text-[10px] text-[var(--color-text-faint)]">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-border-emphasis)]" />
              <span className="h-2 w-24 rounded bg-[var(--color-bg-hover)]" />
            </div>
          ))}
          <p className="pt-1 font-mono text-[10px] text-[var(--color-text-faint)]">
            awaiting signal — timeline will stream here<span className="cursor-block" />
          </p>
        </div>
      ) : (
        <div className="relative space-y-2.5 pl-3">
          <div aria-hidden className="absolute bottom-1 left-[3px] top-1 w-px bg-[var(--color-border-default)]" />
          {entries.map((e, i) => (
            <div key={e.id} className="anim-rise relative">
              <span
                aria-hidden
                className={`absolute -left-3 top-[5px] h-[5px] w-[5px] rounded-full ${
                  e.tone === "crit"
                    ? "bg-[var(--color-accent-red)]"
                    : e.tone === "warn"
                      ? "bg-[var(--color-accent-amber)]"
                      : e.tone === "ok"
                        ? "bg-[var(--color-status-healthy)]"
                        : e.tone === "system"
                          ? "bg-[var(--color-accent-purple)]"
                          : "bg-[var(--color-accent-cyan)]"
                }`}
              />
              <div className="flex items-baseline gap-2 font-mono text-[10px] text-[var(--color-text-faint)]">
                <span>+{String(i).padStart(2, "0")}s</span>
                <span className={e.tone === "system" ? "text-[var(--color-accent-purple)]" : e.tone === "ok" ? "text-[var(--color-status-healthy)]" : "text-[var(--color-accent-cyan)]"}>
                  {e.stage}
                </span>
              </div>
              <p
                className={`text-[11px] leading-snug ${
                  e.tone === "crit"
                    ? "text-[var(--color-accent-red)]"
                    : e.tone === "warn"
                      ? "text-[var(--color-accent-amber)]"
                      : e.tone === "ok"
                        ? "text-[var(--color-status-healthy)]"
                        : e.tone === "system"
                          ? "text-[var(--color-accent-purple)]"
                          : "text-[var(--color-text-secondary)]"
                }`}
              >
                {e.msg}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function formatSec(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

"use client";

import { nodeMap } from "@/lib/scenarios";
import type { Health, PostMortemReport } from "@/lib/types";
import { formatDuration } from "@/lib/incidents";
import { SystemGraph } from "./SystemGraph";
import { Button, Chip, MicroLabel, Panel } from "./ui";
import { IconCheck } from "./icons";

function isoTime(iso: string): string {
  const d = new Date(iso);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

export function PostMortem({ report }: { report: PostMortemReport }) {
  const rcNode = nodeMap[report.rootCauseNode];
  const states: Record<string, Health> = Object.fromEntries(
    Object.keys(nodeMap).map((k) => [k, "healthy"])
  );

  return (
    <div className="mx-auto max-w-[1280px] px-4 py-6">
      {/* masthead */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <a
          href="/"
          className="inline-flex items-center gap-2 rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 font-mono text-[11px] tracking-[0.1em] text-[var(--color-text-secondary)] transition-all duration-200 hover:border-[var(--color-border-emphasis)] hover:text-[var(--color-text-primary)]"
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M19 12H5m0 0 5 5m-5-5 5-5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          COMMAND CENTER
        </a>
        <span className="label-micro text-[var(--color-text-muted)]">Post-incident report</span>
        <span className="ml-auto flex items-center gap-2">
          <Chip tone={report.severity === "P1" ? "crit" : "warn"}>{report.severity}</Chip>
          <Chip tone="ok">RESOLVED</Chip>
          <Chip tone="neutral">{report.autonomy.toLowerCase()}</Chip>
        </span>
      </div>

      <div className="mb-6 border-b border-[var(--color-border-subtle)] pb-5">
        <div className="font-mono text-[12px] tracking-[0.14em] text-[var(--color-accent-cyan)]">
          {report.id}
        </div>
        <h1 className="mt-2 max-w-3xl text-xl font-semibold leading-snug text-[var(--color-text-primary)] md:text-2xl">
          {report.headline}
        </h1>
        <p className="mt-2 font-mono text-[11px] tracking-[0.04em] text-[var(--color-text-muted)]">
          service: <span className="text-[var(--color-text-secondary)]">{report.serviceName}</span> · duration:{" "}
          <span className="text-[var(--color-text-secondary)]">{formatDuration(report.durationSec)}</span> ·{" "}
          {isoTime(report.startedAt)} → {isoTime(report.resolvedAt)} UTC · RCA confidence{" "}
          <span className="text-[var(--color-accent-cyan)]">{report.confidencePct}%</span>
        </p>
      </div>

      {/* topology snapshot */}
      <Panel
        title="Service topology — post-incident"
        right={<span className="label-micro text-[var(--color-text-faint)]">root cause retained for review</span>}
        className="mb-5"
        flush
      >
        <div className="px-3 pt-3">
          <SystemGraph
            states={states}
            rcKey={report.rootCauseNode}
            rcShown
            interactive={false}
            pulses={[]}
          />
        </div>
      </Panel>

      {/* root cause + key facts */}
      <div className="mb-5 grid gap-4 lg:grid-cols-3">
        <Panel title="Root cause analysis" className="lg:col-span-2" bodyClassName="space-y-4">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-[rgba(255,77,103,0.3)] bg-[rgba(255,77,103,0.06)] text-[var(--color-accent-red)]">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M12 2v4m0 12v4M2 12h4m12 0h4" strokeLinecap="round" />
              </svg>
            </span>
            <div>
              <p className="font-mono text-[13px] font-semibold tracking-wide text-[var(--color-accent-red)]">
                {report.rootCauseNode}
              </p>
              <p className="font-mono text-[10px] tracking-[0.14em] text-[var(--color-text-faint)]">
                {rcNode ? rcNode.role.toUpperCase() : ""} · SUSPECTED ROOT CAUSE
              </p>
            </div>
          </div>
          <p className="text-[13px] leading-relaxed text-[var(--color-text-primary)]">{report.rootCause}</p>
          <div className="rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)] p-3">
            <MicroLabel>Impact</MicroLabel>
            <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">{report.impact}</p>
          </div>
          <div className="rounded-md border border-[rgba(0,214,163,0.15)] bg-[rgba(0,214,163,0.03)] p-3">
            <MicroLabel className="text-[var(--color-status-healthy)]">Action taken</MicroLabel>
            <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">{report.actionTaken}</p>
          </div>
        </Panel>

        <Panel title="Key facts" bodyClassName="space-y-2.5">
          {[
            ["Severity", report.severity],
            ["Autonomy tier", report.autonomy],
            ["Affected service", report.serviceName],
            ["Remediation risk", `${report.risk} RISK`],
            ["Duration", formatDuration(report.durationSec)],
            ["Opened", isoTime(report.startedAt)],
            ["Closed", isoTime(report.resolvedAt)],
          ].map(([k, v]) => (
            <div key={k} className="flex items-baseline justify-between gap-3 border-b border-[var(--color-border-subtle)] pb-2 last:border-0">
              <span className="label-micro text-[var(--color-text-muted)]">{k}</span>
              <span className="truncate font-mono text-[12px] text-[var(--color-text-primary)]">{v}</span>
            </div>
          ))}
          <div className="pt-2">
            <div className="mb-1.5 flex justify-between font-mono text-[10px]">
              <span className="tracking-[0.14em] text-[var(--color-text-muted)]">RCA CONFIDENCE</span>
              <span className="text-[var(--color-accent-cyan)]">{report.confidencePct}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-[var(--color-bg-hover)]">
              <div
                className="h-full rounded-full bg-[var(--color-accent-cyan)] transition-all duration-1000"
                style={{ width: `${report.confidencePct}%` }}
              />
            </div>
          </div>
        </Panel>
      </div>

      {/* metrics + checks + prevention */}
      <div className="mb-5 grid gap-4 lg:grid-cols-3">
        <Panel title="Before → after" bodyClassName="space-y-2.5">
          {report.metrics.map((m) => (
            <div key={m.label} className="flex items-center gap-2 border-b border-[var(--color-border-subtle)] pb-2.5 last:border-0">
              <span className="w-28 shrink-0 font-mono text-[10px] text-[var(--color-text-muted)]">{m.label}</span>
              <span className="font-mono text-[11px] text-[var(--color-accent-red)] opacity-80">{m.before}</span>
              <span className="text-[var(--color-text-faint)]">→</span>
              <span className="font-mono text-[11px] text-[var(--color-status-healthy)]">{m.after}</span>
              <span className="ml-auto text-[var(--color-status-healthy)]"><IconCheck className="h-3 w-3" /></span>
            </div>
          ))}
        </Panel>

        <Panel title="Verification checks" bodyClassName="space-y-2">
          {report.checks.map((c) => (
            <div key={c.label} className="flex items-start gap-2.5">
              <span className="mt-0.5 text-[var(--color-status-healthy)]"><IconCheck className="h-3.5 w-3.5" /></span>
              <div>
                <p className="text-[12px] text-[var(--color-text-primary)]">{c.label}</p>
                <p className="font-mono text-[10px] text-[var(--color-status-healthy)] opacity-80">{c.detail}</p>
              </div>
            </div>
          ))}
        </Panel>

        <Panel title="Post-incident actions" bodyClassName="space-y-2">
          {report.prevention.map((p, i) => (
            <p key={i} className="flex items-start gap-2 text-[12px] leading-snug text-[var(--color-text-secondary)]">
              <span className="mt-px text-[var(--color-accent-cyan)]">▸</span>
              {p}
            </p>
          ))}
        </Panel>
      </div>

      {/* timeline */}
      <Panel
        title="Explainable timeline"
        right={<span className="label-micro text-[var(--color-text-muted)]">{report.timeline.length} events</span>}
      >
        <div className="grid gap-x-8 md:grid-cols-2">
          {report.timeline.map((e, i) => (
            <div key={i} className="anim-rise flex gap-3 border-b border-[var(--color-border-subtle)] py-2 last:border-0">
              <span className="w-14 shrink-0 font-mono text-[10px] text-[var(--color-text-faint)]">{e.t}</span>
              <span
                className={`w-20 shrink-0 font-mono text-[10px] tracking-[0.08em] ${
                  e.tone === "crit"
                    ? "text-[var(--color-accent-red)]"
                    : e.tone === "ok"
                      ? "text-[var(--color-status-healthy)]"
                      : e.tone === "warn"
                        ? "text-[var(--color-accent-amber)]"
                        : "text-[var(--color-accent-cyan)] opacity-80"
                }`}
              >
                {e.stage}
              </span>
              <span className="text-[12px] leading-snug text-[var(--color-text-secondary)]">{e.msg}</span>
            </div>
          ))}
        </div>
      </Panel>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-[var(--color-border-subtle)] pt-4">
        <span className="label-micro text-[var(--color-text-faint)]">
          System Bachao · autonomous incident response core
        </span>
        <span className="font-mono text-[10px] text-[var(--color-text-faint)]">
          report auto-generated {isoTime(report.resolvedAt)} UTC · demo dataset · mock telemetry
        </span>
      </div>
    </div>
  );
}

"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { Button, Chip, StatusDot } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { getServiceHealth, listIncidents } from "@/lib/api";
import { KNOWN_SERVICES, type IncidentSummary, type ServiceHealth } from "@/lib/types";

const FLOW = [
  { stage: "Detect", caption: "Failure caught in seconds" },
  { stage: "Investigate", caption: "Evidence gathered across logs & metrics" },
  { stage: "RCA", caption: "Root cause pinpointed with confidence" },
  { stage: "Remediate", caption: "Safe fix applied autonomously" },
  { stage: "Verify", caption: "Recovery proven with checks" },
];

function serviceDisplayName(key: string): string {
  if (!key) return key;
  return key
    .split("-")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-center label-micro text-[var(--color-text-muted)]">
      {children}
    </p>
  );
}

export default function Home() {
  const [health, setHealth] = useState<Record<string, ServiceHealth | null> | null>(null);
  const [recent, setRecent] = useState<IncidentSummary[] | null>(null);

  useEffect(() => {
    let cancelled = false;

    Promise.all(KNOWN_SERVICES.map((svc) => getServiceHealth(svc).then((h) => [svc, h] as const)))
      .then((entries) => {
        if (!cancelled) setHealth(Object.fromEntries(entries));
      })
      .catch(() => {
        if (!cancelled) setHealth({});
      });

    listIncidents(1)
      .then((data) => {
        if (!cancelled) setRecent(data);
      })
      .catch(() => {
        if (!cancelled) setRecent([]);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const healthValues = health ? Object.values(health) : [];
  const allHealthy = healthValues.length > 0 && healthValues.every((h) => h?.health === "healthy");
  const anyDown = healthValues.some((h) => h && h.health !== "healthy" && h.health !== "starting");
  const overallDot = health === null ? "neutral" : anyDown ? "critical" : allHealthy ? "healthy" : "warning";
  const overallLabel =
    health === null ? "Checking systems…" : anyDown ? "Degraded" : allHealthy ? "All Systems Operational" : "Partial data";

  const latest = recent && recent.length > 0 ? recent[0] : null;
  const severityTone = latest?.severity === "P1" ? "crit" : latest?.severity === "P2" ? "warn" : "info";

  return (
    <div className="flex min-h-screen flex-col">
      {/* ── header ── */}
      <header className="sticky top-0 z-40 border-b border-[var(--color-border-subtle)] bg-[var(--color-bg-base)]/75 backdrop-blur-xl">
        <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-6 sm:px-10">
          {/* brand */}
          <div className="flex min-w-0 items-center gap-3">
            <div className="relative grid h-8 w-8 shrink-0 place-items-center">
              <div className="absolute inset-0 rounded-full border border-[rgba(54,215,232,0.15)]" />
              <div className="absolute inset-[3px] rounded-full border border-dashed border-[rgba(255,77,103,0.15)]" />
              <div className="h-2 w-2 rounded-full bg-[var(--color-accent-red)] opacity-80" />
            </div>
            <div className="leading-tight">
              <div className="truncate text-[15px] font-semibold tracking-[0.1em] text-[var(--color-text-primary)]">
                SYSTEM BACHAO
              </div>
              <div className="hidden text-[10px] tracking-[0.16em] text-[var(--color-text-faint)] sm:block">
                AUTONOMOUS INCIDENT RESPONSE
              </div>
            </div>
          </div>

          {/* status + theme + action */}
          <div className="flex shrink-0 items-center gap-3 sm:gap-4">
            <div className="hidden items-center gap-2.5 sm:flex">
              <StatusDot state={overallDot} size="md" pulse={overallDot !== "neutral"} />
              <span className="text-[13px] text-[var(--color-text-secondary)]">{overallLabel}</span>
            </div>
            <ThemeToggle />
            <Link href="/dashboard">
              <Button variant="danger" size="md">
                Open Dashboard
              </Button>
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 pb-32 pt-20 sm:px-10 sm:pt-28">
        {/* ── hero ── */}
        <section className="anim-rise text-center" style={{ animationDelay: "0ms" }}>
          <p className="label-micro text-[var(--color-accent-cyan)] opacity-60">
            Autonomous Enterprise Incident Response
          </p>
          <h1 className="mx-auto mt-5 max-w-3xl text-[40px] font-semibold leading-[1.08] tracking-[-0.03em] text-[var(--color-text-primary)] sm:text-6xl lg:text-[64px]">
            Your system.{" "}
            <span className="bg-gradient-to-r from-[var(--color-accent-cyan)] via-[var(--color-accent-teal)] to-[var(--color-accent-purple)] bg-clip-text text-transparent">
              Protected by AI.
            </span>
          </h1>
          <p className="mx-auto mt-6 max-w-lg text-[15px] leading-relaxed text-[var(--color-text-secondary)] sm:text-[16px]">
            When something breaks, System Bachao detects it, finds the root cause
            and fixes it — with a fully explainable record.
          </p>
        </section>

        {/* ── system status ── */}
        <section className="anim-rise mt-24 sm:mt-32" style={{ animationDelay: "100ms" }}>
          <SectionLabel>System Status</SectionLabel>
          <div className="mx-auto mt-8 grid max-w-3xl grid-cols-1 divide-y divide-[var(--color-border-subtle)] border-y border-[var(--color-border-subtle)] sm:grid-cols-4 sm:divide-x sm:divide-y-0">
            {KNOWN_SERVICES.map((svc) => {
              const status = health?.[svc];
              const dotState =
                status === undefined
                  ? "neutral"
                  : status === null
                    ? "neutral"
                    : status.health === "healthy"
                      ? "healthy"
                      : status.health === "starting"
                        ? "warning"
                        : "critical";
              const label =
                status === undefined ? "Checking…" : status === null ? "Unavailable" : status.health;
              return (
                <div
                  key={svc}
                  className="group flex flex-col items-center gap-3 py-8 transition-colors duration-200 hover:bg-[var(--color-bg-surface)] sm:py-10"
                >
                  <span className="text-[15px] font-medium tracking-tight text-[var(--color-text-primary)] text-center">
                    {serviceDisplayName(svc)}
                  </span>
                  <div className="flex items-center gap-2">
                    <StatusDot state={dotState} size="md" pulse={dotState === "healthy"} />
                    <span
                      className={`text-[13px] font-medium capitalize ${
                        dotState === "healthy"
                          ? "text-[var(--color-status-healthy)]"
                          : dotState === "critical"
                            ? "text-[var(--color-accent-red)]"
                            : "text-[var(--color-text-muted)]"
                      }`}
                    >
                      {label}
                    </span>
                  </div>
                  {status?.version && (
                    <span className="font-mono text-[11px] text-[var(--color-text-faint)]">{status.version}</span>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        {/* ── AI response flow ── */}
        <section className="anim-rise mt-24 sm:mt-32" style={{ animationDelay: "200ms" }}>
          <SectionLabel>AI Response Flow</SectionLabel>
          <div className="mt-10 flex flex-col items-center gap-6 md:flex-row md:items-start md:justify-between md:gap-0">
            {FLOW.map((step, i) => (
              <Fragment key={step.stage}>
                {i > 0 && (
                  <div aria-hidden className="flex items-center md:self-center">
                    <svg
                      viewBox="0 0 24 24"
                      className="h-4 w-4 rotate-90 text-[var(--color-text-faint)] md:rotate-0 md:mx-2"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.5"
                    >
                      <path d="M5 12h14m0 0-5-5m5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </div>
                )}
                <div className="flex flex-col items-center gap-2 text-center md:flex-1 md:px-3">
                  <span className="font-mono text-[10px] tracking-[0.2em] text-[var(--color-text-faint)]">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="text-[13px] font-semibold text-[var(--color-text-primary)]">{step.stage}</span>
                  <span className="max-w-[200px] text-[12px] leading-relaxed text-[var(--color-text-muted)]">
                    {step.caption}
                  </span>
                </div>
              </Fragment>
            ))}
          </div>
        </section>

        {/* ── recent incident ── */}
        <section className="anim-rise mt-24 sm:mt-32" style={{ animationDelay: "300ms" }}>
          <div className="mx-auto max-w-3xl">
            <SectionLabel>Recent Incident</SectionLabel>
            <div className="mt-6 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-6 py-6 transition-colors duration-200 hover:border-[var(--color-border-emphasis)] sm:px-8 sm:py-7">
              {recent === null ? (
                <p className="text-center text-[13px] text-[var(--color-text-muted)]">Loading…</p>
              ) : !latest ? (
                <div className="flex flex-col items-center gap-4 text-center">
                  <span className="text-[14px] text-[var(--color-text-secondary)]">
                    No incidents yet. Trigger one from the dashboard to see the AI core respond in real time.
                  </span>
                  <Link href="/dashboard">
                    <Button variant="primary" size="md">
                      Open Dashboard
                    </Button>
                  </Link>
                </div>
              ) : (
                <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex flex-col gap-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="font-mono text-[13px] tracking-[0.08em] text-[var(--color-text-secondary)]">
                        {latest.id.slice(0, 8)}…
                      </span>
                      {latest.severity && <Chip tone={severityTone}>{latest.severity}</Chip>}
                    </div>
                    <span className="text-[22px] font-semibold tracking-tight text-[var(--color-text-primary)] sm:text-[26px]">
                      {serviceDisplayName(latest.service_name)}
                    </span>
                    <span className="text-[13px] text-[var(--color-text-muted)] leading-relaxed max-w-md">
                      state: {latest.state} · stage: {latest.current_stage ?? "—"}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-6 sm:flex-col sm:items-end">
                    <div className="flex items-center gap-2">
                      <StatusDot
                        state={latest.state === "resolved" ? "healthy" : latest.state === "failed" || latest.state === "escalated" ? "critical" : "warning"}
                        size="md"
                        pulse
                      />
                      <span className="text-[13px] font-medium text-[var(--color-text-secondary)] capitalize">
                        {latest.state}
                      </span>
                    </div>
                    <Link
                      href={`/incidents/${latest.id}`}
                      className="group inline-flex items-center gap-2 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-[13px] font-medium text-[var(--color-text-primary)] transition-all duration-200 hover:border-[var(--color-border-emphasis)] hover:bg-[var(--color-bg-hover)] active:scale-[0.98]"
                    >
                      View Incident
                      <svg
                        viewBox="0 0 24 24"
                        className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <path d="M5 12h14m0 0-5-5m5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </Link>
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

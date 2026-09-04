"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { SCENARIOS } from "@/lib/scenarios";
import type { SimulationState } from "@/lib/types";
import { IconTriangle } from "./icons";
import { StatusDot } from "./ui";
import { ThemeToggle } from "./ThemeToggle";

const RUNNING_PHASES = new Set([
  "detect",
  "triage",
  "investigate",
  "rca",
  "awaiting_approval",
  "executing",
  "escalated",
  "verify",
]);

function clock(): string {
  const d = new Date();
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

export function TopBar({
  sim,
  scenarioKey,
  onScenario,
  onSimulate,
  onReset,
}: {
  sim: SimulationState;
  scenarioKey: string;
  onScenario: (key: string) => void;
  onSimulate: () => void;
  onReset: () => void;
}) {
  const [now, setNow] = useState(clock);
  useEffect(() => {
    const iv = window.setInterval(() => setNow(clock()), 1000);
    return () => window.clearInterval(iv);
  }, []);

  const running = RUNNING_PHASES.has(sim.phase);
  const status =
    sim.phase === "standby"
      ? { text: "AI CORE ARMED", dot: "healthy" as const }
      : sim.phase === "resolved"
        ? { text: "INCIDENT CLOSED", dot: "healthy" as const }
        : sim.phase === "escalated"
          ? { text: "ESCALATED — ON-CALL", dot: "critical" as const }
          : { text: `RESPONDING · ${sim.incident?.id ?? ""}`, dot: "warning" as const };

  return (
    <header className="sticky top-0 z-40 border-b border-[var(--color-border-subtle)] bg-[var(--color-bg-base)]/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1760px] items-center gap-4 px-4 py-2.5">
        {/* brand — back to overview */}
        <Link href="/" className="flex min-w-0 items-center gap-3">
          <div className="relative grid h-9 w-9 shrink-0 place-items-center">
            <div className="absolute inset-0 rounded-full border border-[rgba(54,215,232,0.25)]" />
            <div className="absolute inset-[3px] rounded-full border border-dashed border-[rgba(255,77,103,0.3)]" style={{ animation: "kf-rotate 14s linear infinite" }} />
            <div className="h-3 w-3 rounded-full bg-[var(--color-accent-red)] shadow-[0_0_12px_rgba(255,77,103,0.7)]" />
            <div className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-[var(--color-accent-cyan)] shadow-[0_0_8px_rgba(54,215,232,0.8)]" />
          </div>
          <div className="min-w-0 leading-tight">
            <div className="truncate text-[15px] font-semibold tracking-[0.12em] text-[var(--color-text-primary)]">
              SYSTEM BACHAO
            </div>
            <div className="label-micro text-[var(--color-text-faint)]">
              Autonomous Enterprise Incident Response
            </div>
          </div>
        </Link>

        <div className="mx-1 hidden h-7 w-px bg-[var(--color-border-subtle)] md:block" />

        {/* status */}
        <div className="hidden items-center gap-2 md:flex">
          <StatusDot state={status.dot} size="md" pulse={running} />
          <span className={`font-mono text-[11px] tracking-[0.14em] ${status.dot === "healthy" ? "text-[var(--color-status-healthy)]" : status.dot === "critical" ? "text-[var(--color-accent-red)]" : "text-[var(--color-accent-cyan)]"}`}>
            {status.text}
          </span>
        </div>

        <div className="ml-auto flex items-center gap-3">
          {/* scenario picker */}
          <div className="hidden items-center gap-1.5 lg:flex">
            <span className="label-micro mr-1 text-[var(--color-text-faint)]">Fault</span>
            {SCENARIOS.map((s) => {
              const sel = scenarioKey === s.key;
              return (
                <button
                  key={s.key}
                  disabled={running}
                  onClick={() => onScenario(s.key)}
                  title={s.tagline}
                  className={`rounded-md border px-2.5 py-1.5 font-mono text-[11px] tracking-[0.06em] transition-all duration-200 ${
                    sel
                      ? "border-[rgba(255,77,103,0.4)] bg-[rgba(255,77,103,0.08)] text-[var(--color-accent-red)]"
                      : "border-[var(--color-border-default)] text-[var(--color-text-muted)] hover:border-[var(--color-border-emphasis)] hover:text-[var(--color-text-secondary)]"
                  } ${running ? "cursor-not-allowed opacity-40" : ""}`}
                >
                  <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${s.chip.dot}`} />
                  {s.label}
                </button>
              );
            })}
          </div>

          <div className="hidden h-7 w-px bg-[var(--color-border-subtle)] xl:block" />

          {/* theme toggle */}
          <ThemeToggle />

          {/* clock */}
          <div className="hidden text-right font-mono xl:block">
            <div className="text-[12px] font-medium tabular-nums text-[var(--color-text-primary)]">{now}</div>
            <div className="text-[9px] tracking-[0.18em] text-[var(--color-text-faint)]">UTC LOCAL</div>
          </div>

          {/* actions */}
          {running ? (
            <button
              onClick={onReset}
              className="rounded-lg border border-[rgba(255,77,103,0.4)] bg-[rgba(255,77,103,0.08)] px-3.5 py-2 font-mono text-[11px] font-semibold tracking-[0.14em] text-[var(--color-accent-red)] transition-all duration-200 hover:border-[rgba(255,77,103,0.6)] hover:bg-[rgba(255,77,103,0.15)] active:scale-[0.98]"
            >
              ABORT
            </button>
          ) : (
            <>
              <button
                onClick={onSimulate}
                className="group relative overflow-hidden rounded-lg border border-[rgba(255,77,103,0.5)] bg-gradient-to-b from-[rgba(255,77,103,0.2)] to-[rgba(255,77,103,0.08)] px-4 py-2 font-mono text-[11px] font-semibold tracking-[0.16em] text-[var(--color-accent-red)] shadow-[0_0_22px_-6px_rgba(255,77,103,0.6)] transition-all duration-200 hover:shadow-[0_0_30px_-4px_rgba(255,77,103,0.8)] hover:brightness-125 active:scale-[0.98]"
              >
                <span
                  aria-hidden
                  className="stripes-red absolute inset-0 opacity-50 transition-opacity group-hover:opacity-70"
                />
                <span className="relative flex items-center gap-1.5">
                  <IconTriangle className="h-2.5 w-2.5" />
                  {sim.phase === "resolved" ? "SIMULATE AGAIN" : "SIMULATE FAILURE"}
                </span>
              </button>
              {sim.phase === "resolved" && (
                <button
                  onClick={onReset}
                  className="rounded-lg border border-[var(--color-border-default)] px-3 py-2 font-mono text-[10px] tracking-[0.14em] text-[var(--color-text-muted)] transition-all duration-200 hover:border-[var(--color-border-emphasis)] hover:text-[var(--color-text-secondary)]"
                >
                  RESET
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </header>
  );
}

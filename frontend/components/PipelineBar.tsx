"use client";

import type { EnginePhase } from "@/lib/types";
import { IconCheck } from "./icons";

const STEPS = ["DETECT", "TRIAGE", "INVESTIGATE", "RCA", "REMEDIATE", "VERIFY"] as const;

const SUBTITLE: Record<string, { done: string; active: string }> = {
  DETECT: { done: "ALERT OPENED", active: "SCANNING TELEMETRY" },
  TRIAGE: { done: "BLAST MAPPED", active: "CORRELATING" },
  INVESTIGATE: { done: "EVIDENCE GATHERED", active: "PARALLEL AGENTS" },
  RCA: { done: "CAUSE ISOLATED", active: "PINPOINTING" },
  REMEDIATE: { done: "APPLIED", active: "AWAITING APPROVAL" },
  VERIFY: { done: "PASSED", active: "CHECKING TELEMETRY" },
};

export function PipelineBar({
  stageIdx,
  stagesDone,
  phase,
}: {
  stageIdx: number;
  stagesDone: boolean[];
  phase: EnginePhase;
}) {
  const running = phase !== "standby" && phase !== "resolved";
  const phaseNum = stageIdx >= 0 ? stageIdx + 1 : stagesDone.every(Boolean) ? 6 : 0;

  return (
    <div>
      <div className="mb-2 flex items-center justify-between px-0.5">
        <span className="label-micro text-[var(--color-text-muted)]">Autonomous response pipeline</span>
        <span className="font-mono text-[10px] tracking-[0.14em] text-[var(--color-text-muted)]">
          {phase === "standby"
            ? "ARMED · WAITING FOR SIGNAL"
            : phase === "resolved"
              ? "RUN COMPLETE"
              : `PHASE ${String(phaseNum).padStart(2, "0")}/06 · ${STEPS[stageIdx] ?? ""}`}
        </span>
      </div>

      <div className="flex items-stretch gap-2">
        {STEPS.map((name, i) => {
          const done = stagesDone[i] ?? false;
          const active = running && stageIdx === i;
          const rejected = name === "REMEDIATE" && phase === "escalated";

          return (
            <div key={name} className="flex min-w-0 flex-1 items-center gap-2">
              <div
                className={`relative flex h-[56px] min-w-0 flex-1 flex-col justify-center overflow-hidden rounded-md border px-3 transition-all duration-500 ${
                  done
                    ? "border-[rgba(0,214,163,0.2)] bg-[rgba(0,214,163,0.04)]"
                    : active
                      ? "border-[rgba(54,215,232,0.3)] bg-[rgba(54,215,232,0.06)]"
                      : rejected
                        ? "border-[rgba(255,77,103,0.35)] bg-[rgba(255,77,103,0.06)]"
                        : "border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)]"
                } ${active ? "shadow-[0_0_20px_-4px_rgba(54,215,232,0.3)]" : ""}`}
              >
                {/* active shimmer */}
                {active && (
                  <div
                    aria-hidden
                    className="shine absolute inset-0"
                    style={{
                      background:
                        "linear-gradient(110deg, transparent 30%, rgba(54,215,232,0.08) 50%, transparent 70%)",
                    }}
                  />
                )}
                <div className="flex items-center gap-2">
                  <span
                    className={`font-mono text-[10px] ${done ? "text-[var(--color-status-healthy)]" : active ? "text-[var(--color-accent-cyan)]" : "text-[var(--color-text-faint)]"}`}
                  >
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span
                    className={`truncate text-[11px] font-semibold tracking-[0.1em] ${
                      done
                        ? "text-[var(--color-status-healthy)]"
                        : active
                          ? "text-[var(--color-accent-cyan)]"
                          : rejected
                            ? "text-[var(--color-accent-red)]"
                            : "text-[var(--color-text-muted)]"
                    }`}
                  >
                    {name}
                  </span>
                  {done ? (
                    <IconCheck className="ml-auto h-3 w-3 shrink-0 text-[var(--color-status-healthy)]" />
                  ) : active ? (
                    <span className="blink ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent-cyan)]" />
                  ) : null}
                </div>
                <div
                  className={`mt-1 truncate font-mono text-[9px] tracking-[0.12em] ${
                    done
                      ? "text-[var(--color-status-healthy)] opacity-70"
                      : active
                        ? "text-[var(--color-accent-cyan)] opacity-70"
                        : rejected
                          ? "text-[var(--color-accent-red)] opacity-80"
                          : "text-[var(--color-text-faint)]"
                  }`}
                >
                  {done
                    ? SUBTITLE[name].done
                    : active
                      ? rejected
                        ? "REJECTED · ESCALATED"
                        : SUBTITLE[name].active
                      : rejected
                        ? "REJECTED · ESCALATED"
                        : ""}
                </div>

                {/* progress underline on active step */}
                {active && (
                  <div
                    aria-hidden
                    className="absolute inset-x-0 bottom-0 h-[2px] origin-left bg-gradient-to-r from-[var(--color-accent-cyan)] via-[var(--color-accent-teal)] to-transparent"
                    style={{ animation: "kf-growx 1.2s ease-out infinite" }}
                  />
                )}
              </div>

              {i < STEPS.length - 1 && (
                <svg viewBox="0 0 8 14" className="h-3.5 w-2 shrink-0 text-[var(--color-text-faint)]" aria-hidden>
                  <path d="M1 1l6 6-6 6" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" />
                </svg>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

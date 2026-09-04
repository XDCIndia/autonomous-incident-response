"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSimulation } from "@/hooks/useSimulation";
import { takePendingScenario } from "@/lib/incidents";
import { AiConsole } from "./AiConsole";
import { CommandPanel } from "./CommandPanel";
import { PipelineBar } from "./PipelineBar";
import { SystemGraph } from "./SystemGraph";
import { TopBar } from "./TopBar";
import { Panel } from "./ui";

export function LiveIncident() {
  const { sim, start, approve, reject, confirmManual, reset } = useSimulation();
  const router = useRouter();
  const [scenarioKey, setScenarioKey] = useState<string>("bad_deployment");
  const [booted, setBooted] = useState(false);
  const bootRef = useRef(false);
  const handedOffRef = useRef(false);

  // cinematic boot flash on entry
  useEffect(() => {
    const t = window.setTimeout(() => setBooted(true), 1200);
    return () => window.clearTimeout(t);
  }, []);

  // auto-start a scenario requested from the home page
  useEffect(() => {
    if (bootRef.current) return;
    bootRef.current = true;
    const key = takePendingScenario();
    if (key) {
      setScenarioKey(key);
      start(key);
    }
  }, [start]);

  // once resolved, linger on the recovery moment, then hand off to the report page
  useEffect(() => {
    if (sim.phase !== "resolved" || !sim.report || handedOffRef.current) return;
    handedOffRef.current = true;
    const reportId = sim.report.id;
    const t = window.setTimeout(() => {
      router.replace(`/incidents/${reportId}`);
    }, 4200);
    return () => window.clearTimeout(t);
  }, [sim.phase, sim.report, router]);

  const healthy = Object.values(sim.nodeHealth).filter((h) => h === "healthy").length;
  const degraded = Object.values(sim.nodeHealth).filter((h) => h === "degraded").length;
  const down = Object.values(sim.nodeHealth).filter((h) => h === "down").length;

  return (
    <div className="flex min-h-screen flex-col">
      <TopBar
        sim={sim}
        scenarioKey={scenarioKey}
        onScenario={setScenarioKey}
        onSimulate={() => start(scenarioKey)}
        onReset={reset}
      />

      <main className="mx-auto w-full max-w-[1760px] flex-1 px-4 py-4 lg:h-[calc(100vh-58px)] lg:overflow-hidden">
        <div className="grid h-full grid-cols-1 gap-4 lg:grid-cols-12">
          {/* left — incident command + explainable timeline */}
          <aside className="min-h-0 lg:col-span-3">
            <CommandPanel sim={sim} />
          </aside>

          {/* center — architecture + pipeline */}
          <section className="flex min-h-0 flex-col gap-4 lg:col-span-6">
            <Panel
              title="System architecture"
              className="flex min-h-0 flex-1 flex-col"
              bodyClassName="min-h-0 flex-1 overflow-y-auto"
              flush
              right={
                <span className="font-mono text-[10px] tracking-[0.08em] text-[var(--color-text-muted)]">
                  <span className="text-[var(--color-status-healthy)]">● {healthy}</span>{" "}
                  <span className="mx-1 text-[var(--color-text-faint)]">/</span>
                  <span className="text-[var(--color-accent-amber)]">● {degraded}</span>{" "}
                  <span className="mx-1 text-[var(--color-text-faint)]">/</span>
                  <span className={down > 0 ? "blink text-[var(--color-accent-red)]" : "text-[var(--color-accent-red)]"}>● {down}</span>
                  <span className="ml-2 hidden text-[var(--color-text-faint)] sm:inline">FAILED</span>
                </span>
              }
            >
              <div className="p-3">
                <SystemGraph
                  states={sim.nodeHealth}
                  pulses={sim.pulses}
                  rcKey={sim.rcNode}
                  rcShown={sim.rcShown}
                  scanning={sim.phase === "investigate"}
                  recovery={sim.phase === "resolved" && !!sim.incident}
                  recoveryId={sim.incident?.id}
                />
              </div>
            </Panel>

            <PipelineBar
              stageIdx={sim.stageIdx}
              stagesDone={sim.stagesDone}
              phase={sim.phase}
            />
          </section>

          {/* right — AI investigation console */}
          <aside className="flex min-h-0 flex-col gap-4 lg:col-span-3">
            <AiConsole sim={sim} onApprove={approve} onReject={reject} onConfirmManual={confirmManual} />
          </aside>
        </div>
      </main>

      <footer className="border-t border-[var(--color-border-subtle)] py-2.5">
        <div className="mx-auto flex max-w-[1760px] items-center justify-between px-4 font-mono text-[9px] tracking-[0.14em] text-[var(--color-text-faint)]">
          <span>SYSTEM BACHAO v2.4.1 · AI INCIDENT COMMAND CENTER</span>
          <span className="hidden sm:inline">DEMO FRONTEND · MOCK TELEMETRY · NO BACKEND REQUIRED</span>
        </div>
      </footer>

      {/* cinematic boot flash */}
      {!booted && (
        <div className="anim-fade fixed inset-0 z-50 grid place-items-center bg-[var(--color-bg-base)]">
          <div className="text-center">
            <p className="label-micro text-[var(--color-text-muted)]">system bachao</p>
            <p className="mt-3 font-mono text-[12px] tracking-[0.2em] text-[var(--color-accent-cyan)]">
              neural core online
              <span className="cursor-block" />
            </p>
            <p className="mt-1.5 font-mono text-[10px] tracking-[0.14em] text-[var(--color-text-faint)]">
              linking telemetry · arming response pipeline
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Button, Chip, MicroLabel, Panel, StatusDot } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { ApiError, getServiceHealth, listIncidents, searchKnowledgeBase, triggerIncident } from "@/lib/api";
import {
  KNOWN_SERVICES,
  SCENARIOS,
  type IncidentSummary,
  type KnowledgeBaseResult,
  type SeverityLevel,
  type ServiceHealth,
} from "@/lib/types";

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

type FetchState<T> = { status: "loading" | "ready" | "error"; data: T | null; error: string | null };

export default function Dashboard() {
  const router = useRouter();

  const [health, setHealth] = useState<FetchState<Record<string, ServiceHealth | null>>>({
    status: "loading",
    data: null,
    error: null,
  });
  const [incidents, setIncidents] = useState<FetchState<IncidentSummary[]>>({
    status: "loading",
    data: null,
    error: null,
  });

  const [triggering, setTriggering] = useState<string | null>(null);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  const [kbQuery, setKbQuery] = useState("");
  const [kbState, setKbState] = useState<FetchState<KnowledgeBaseResult[]>>({
    status: "ready",
    data: null,
    error: null,
  });

  const loadHealth = useCallback(async () => {
    try {
      const entries = await Promise.all(
        KNOWN_SERVICES.map(async (svc) => [svc, await getServiceHealth(svc)] as const)
      );
      setHealth({ status: "ready", data: Object.fromEntries(entries), error: null });
    } catch (e) {
      setHealth({ status: "error", data: null, error: e instanceof ApiError ? e.message : "Unable to load service health" });
    }
  }, []);

  const loadIncidents = useCallback(async () => {
    try {
      const data = await listIncidents();
      setIncidents({ status: "ready", data, error: null });
    } catch (e) {
      setIncidents({ status: "error", data: null, error: e instanceof ApiError ? e.message : "Unable to load incidents" });
    }
  }, []);

  useEffect(() => {
    loadHealth();
    loadIncidents();
    const interval = setInterval(() => {
      loadHealth();
      loadIncidents();
    }, 8000);
    return () => clearInterval(interval);
  }, [loadHealth, loadIncidents]);

  const handleTrigger = async (scenarioId: string, service: string) => {
    setTriggering(scenarioId);
    setTriggerError(null);
    try {
      const res = await triggerIncident(service, scenarioId);
      router.push(`/incidents/${res.incident_id}`);
    } catch (e) {
      setTriggerError(e instanceof ApiError ? e.message : "Unable to trigger incident");
      setTriggering(null);
    }
  };

  const handleKbSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = kbQuery.trim();
    if (!q) return;
    setKbState({ status: "loading", data: null, error: null });
    try {
      const res = await searchKnowledgeBase(q);
      setKbState({ status: "ready", data: res.results, error: null });
    } catch (e) {
      setKbState({ status: "error", data: null, error: e instanceof ApiError ? e.message : "Search failed" });
    }
  };

  const healthValues = health.data ? Object.values(health.data) : [];
  const anyDown = healthValues.some((h) => h && h.health !== "healthy" && h.health !== "starting");
  const allHealthy = healthValues.length > 0 && healthValues.every((h) => h?.health === "healthy");
  const overallDot = health.status !== "ready" ? "neutral" : anyDown ? "critical" : allHealthy ? "healthy" : "warning";
  const overallLabel =
    health.status === "loading"
      ? "Checking…"
      : health.status === "error"
        ? "Health unavailable"
        : anyDown
          ? "Degraded"
          : allHealthy
            ? "All Systems Operational"
            : "Partial data";

  return (
    <div className="min-h-screen">
      {/* ── header ── */}
      <header className="glass-nav sticky top-0 z-40">
        <div className="mx-auto flex max-w-[1280px] items-center gap-4 px-4 py-3">
          <Link href="/" className="flex min-w-0 items-center gap-3">
            <div className="relative grid h-8 w-8 shrink-0 place-items-center">
              <div className="absolute inset-0 rounded-full border border-[rgba(54,215,232,0.2)]" />
              <div className="h-2 w-2 rounded-full bg-[var(--color-accent-red)] opacity-80" />
            </div>
            <div className="leading-tight">
              <div className="text-[14px] font-semibold tracking-[0.1em] text-[var(--color-text-primary)]">
                SYSTEM BACHAO
              </div>
              <div className="label-micro text-[var(--color-text-faint)]">Operations Dashboard</div>
            </div>
          </Link>

          <div className="hidden items-center gap-2 md:flex">
            <StatusDot state={overallDot} size="md" pulse={overallDot !== "neutral"} />
            <span className="font-mono text-[11px] tracking-[0.1em] text-[var(--color-text-secondary)]">
              {overallLabel}
            </span>
          </div>

          <div className="ml-auto flex items-center gap-3">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1280px] px-4 py-8 space-y-8">
        {/* ── service health ── */}
        <Panel
          title="Service Health"
          right={
            <button
              onClick={loadHealth}
              className="label-micro text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
            >
              Refresh
            </button>
          }
        >
          {health.status === "error" ? (
            <div className="flex items-center justify-between gap-4 rounded-md border border-[rgba(255,77,103,0.25)] bg-[rgba(255,77,103,0.05)] px-4 py-3 text-[13px] text-[var(--color-accent-red)]">
              <span>{health.error}</span>
              <button onClick={loadHealth} className="font-medium underline">
                Retry
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {KNOWN_SERVICES.map((svc) => {
                const status = health.data?.[svc];
                const dotState =
                  health.status === "loading"
                    ? "neutral"
                    : !status
                      ? "neutral"
                      : status.health === "healthy"
                        ? "healthy"
                        : status.health === "starting"
                          ? "warning"
                          : "critical";
                const label = health.status === "loading" ? "checking…" : !status ? "unavailable" : status.health;
                return (
                  <div
                    key={svc}
                    className="rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)] px-3.5 py-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[13px] font-medium text-[var(--color-text-primary)]">
                        {serviceDisplayName(svc)}
                      </span>
                      <StatusDot state={dotState} size="sm" pulse={dotState === "healthy"} />
                    </div>
                    <div className="mt-1.5 flex items-center justify-between">
                      <span className="font-mono text-[11px] capitalize text-[var(--color-text-muted)]">{label}</span>
                      {status?.version && (
                        <span className="font-mono text-[10px] text-[var(--color-text-faint)]">{status.version}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Panel>

        {/* ── trigger incident ── */}
        <Panel title="Trigger Incident">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {SCENARIOS.map((s) => {
              const busy = triggering === s.id;
              const disabled = triggering !== null;
              return (
                <button
                  key={s.id}
                  onClick={() => handleTrigger(s.id, s.service)}
                  disabled={disabled}
                  className={`flex flex-col gap-2 rounded-md border px-4 py-4 text-left transition-all duration-200 ${
                    disabled
                      ? "cursor-not-allowed opacity-50 border-[var(--color-border-subtle)]"
                      : "border-[var(--color-border-default)] hover:border-[rgba(255,77,103,0.4)] hover:bg-[rgba(255,77,103,0.05)] active:scale-[0.98]"
                  }`}
                >
                  <span className="text-[14px] font-semibold text-[var(--color-text-primary)]">
                    {busy ? "Triggering…" : s.label}
                  </span>
                  <span className="text-[12px] leading-relaxed text-[var(--color-text-muted)]">{s.description}</span>
                  <span className="font-mono text-[11px] text-[var(--color-text-faint)]">{s.service}</span>
                </button>
              );
            })}
          </div>
          {triggerError && (
            <div className="mt-3 rounded-md border border-[rgba(255,77,103,0.25)] bg-[rgba(255,77,103,0.05)] px-4 py-3 text-[13px] text-[var(--color-accent-red)]">
              {triggerError}
            </div>
          )}
        </Panel>

        {/* ── recent incidents ── */}
        <Panel
          title="Recent Incidents"
          right={
            <button
              onClick={loadIncidents}
              className="label-micro text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
            >
              Refresh
            </button>
          }
        >
          {incidents.status === "loading" ? (
            <p className="text-[13px] text-[var(--color-text-muted)]">Loading…</p>
          ) : incidents.status === "error" ? (
            <div className="flex items-center justify-between gap-4 rounded-md border border-[rgba(255,77,103,0.25)] bg-[rgba(255,77,103,0.05)] px-4 py-3 text-[13px] text-[var(--color-accent-red)]">
              <span>{incidents.error}</span>
              <button onClick={loadIncidents} className="font-medium underline">
                Retry
              </button>
            </div>
          ) : incidents.data && incidents.data.length === 0 ? (
            <p className="text-[13px] text-[var(--color-text-muted)]">No incidents yet. Trigger one above.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse text-[13px]">
                <thead>
                  <tr className="border-b border-[var(--color-border-subtle)] text-left">
                    <th className="pb-2 pr-4 label-micro font-normal">ID</th>
                    <th className="pb-2 pr-4 label-micro font-normal">Service</th>
                    <th className="pb-2 pr-4 label-micro font-normal">State</th>
                    <th className="pb-2 pr-4 label-micro font-normal">Severity</th>
                    <th className="pb-2 pr-4 label-micro font-normal">Stage</th>
                    <th className="pb-2 label-micro font-normal">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.data?.map((inc) => (
                    <tr
                      key={inc.id}
                      className="cursor-pointer border-b border-[var(--color-border-subtle)] transition-colors hover:bg-[var(--color-bg-hover)]"
                      onClick={() => router.push(`/incidents/${inc.id}`)}
                    >
                      <td className="py-2.5 pr-4">
                        <Link
                          href={`/incidents/${inc.id}`}
                          className="font-mono text-[var(--color-accent-cyan)] hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {inc.id.slice(0, 8)}…
                        </Link>
                      </td>
                      <td className="py-2.5 pr-4 text-[var(--color-text-primary)]">{serviceDisplayName(inc.service_name)}</td>
                      <td className="py-2.5 pr-4">
                        <Chip tone={stateTone(inc.state)}>{inc.state}</Chip>
                      </td>
                      <td className="py-2.5 pr-4">
                        {inc.severity ? <Chip tone={severityTone(inc.severity)}>{inc.severity}</Chip> : "—"}
                      </td>
                      <td className="py-2.5 pr-4 text-[var(--color-text-muted)]">{inc.current_stage ?? "—"}</td>
                      <td className="py-2.5 text-[var(--color-text-muted)]">
                        {new Date(inc.created_at).toLocaleTimeString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        {/* ── knowledge base ── */}
        <Panel title="Knowledge Base">
          <form onSubmit={handleKbSearch} className="flex gap-2">
            <input
              type="text"
              value={kbQuery}
              onChange={(e) => setKbQuery(e.target.value)}
              placeholder="Search past incidents, e.g. 'database timeout'"
              className="flex-1 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent-cyan)]"
            />
            <Button variant="secondary" size="md" disabled={kbState.status === "loading" || !kbQuery.trim()}>
              {kbState.status === "loading" ? "Searching…" : "Search"}
            </Button>
          </form>

          {kbState.status === "error" && (
            <div className="mt-3 rounded-md border border-[rgba(255,77,103,0.25)] bg-[rgba(255,77,103,0.05)] px-4 py-3 text-[13px] text-[var(--color-accent-red)]">
              {kbState.error}
            </div>
          )}

          {kbState.data !== null && kbState.status === "ready" && (
            kbState.data.length === 0 ? (
              <p className="mt-3 text-[13px] text-[var(--color-text-muted)]">No similar past incidents found.</p>
            ) : (
              <div className="mt-3 flex flex-col gap-2">
                {kbState.data.map((r) => (
                  <div
                    key={r.id}
                    className="rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)] px-4 py-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[13px] font-medium text-[var(--color-text-primary)]">
                        {serviceDisplayName(r.service)}
                      </span>
                      <MicroLabel>{(r.similarity * 100).toFixed(0)}% match</MicroLabel>
                    </div>
                    <p className="mt-1 text-[13px] text-[var(--color-text-secondary)]">{r.description}</p>
                    <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                      root cause: {r.root_cause} · resolved via: {r.resolved_via}
                    </p>
                  </div>
                ))}
              </div>
            )
          )}
        </Panel>
      </main>
    </div>
  );
}

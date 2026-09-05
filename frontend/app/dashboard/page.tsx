"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Button, Chip, MicroLabel, Panel, StatusDot } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useScrollReveal } from "@/hooks/useScrollReveal";
import {
  ApiError,
  createTarget,
  deleteTarget,
  getServiceHealth,
  listIncidents,
  listTargets,
  searchKnowledgeBase,
  setTargetMonitoring,
  triggerIncident,
} from "@/lib/api";
import {
  KNOWN_SERVICES,
  SCENARIOS,
  type IncidentSummary,
  type KnowledgeBaseResult,
  type MonitoredTarget,
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

function targetHealthDot(target: MonitoredTarget): "healthy" | "warning" | "critical" | "neutral" {
  if (!target.monitoring_enabled) return "neutral";
  if (target.health_status === "healthy") return "healthy";
  if (target.health_status === "unhealthy") return target.incident_reported ? "critical" : "warning";
  return "neutral";
}

/**
 * Resolves the incident to link a target to. url_monitor incidents can reach
 * a terminal state in well under a second (recommendation-only remediation,
 * single HTTP verification), so target.active_incident_id — which clears the
 * moment an incident goes terminal — is almost never populated by the time a
 * user looks at the dashboard. Cross-referencing the already-loaded incident
 * list (same GET /incidents call Recent Incidents uses, no new endpoint) by
 * service_name === target.name recovers the link for that common case too.
 */
function linkedIncidentId(target: MonitoredTarget, incidents: IncidentSummary[] | null): string | null {
  if (target.active_incident_id) return target.active_incident_id;
  if (!target.incident_reported || !incidents) return null;
  const match = incidents.find((i) => i.service_name === target.name);
  return match?.id ?? null;
}

type FetchState<T> = { status: "loading" | "ready" | "error"; data: T | null; error: string | null };

function ServiceHealthCard({
  svc,
  status,
  loading,
  delay,
}: {
  svc: string;
  status: ServiceHealth | null | undefined;
  loading: boolean;
  delay: number;
}) {
  const reveal = useScrollReveal<HTMLDivElement>(delay);
  const dotState = loading ? "neutral" : !status ? "neutral" : status.health === "healthy" ? "healthy" : status.health === "starting" ? "warning" : "critical";
  const label = loading ? "checking…" : !status ? "unavailable" : status.health;
  return (
    <div
      ref={reveal.ref}
      style={reveal.style}
      className={`glow-card bg-[var(--color-bg-surface)] px-3.5 py-3 ${reveal.className}`}
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
}

function ScenarioButton({
  scenario,
  busy,
  disabled,
  delay,
  onTrigger,
}: {
  scenario: { id: string; label: string; description: string; service: string };
  busy: boolean;
  disabled: boolean;
  delay: number;
  onTrigger: () => void;
}) {
  const reveal = useScrollReveal<HTMLButtonElement>(delay);
  return (
    <button
      ref={reveal.ref}
      style={reveal.style}
      onClick={onTrigger}
      disabled={disabled}
      className={`flex flex-col gap-2 px-4 py-4 text-left transition-all duration-200 ${reveal.className} ${
        disabled
          ? "cursor-not-allowed opacity-50 rounded-md border border-[var(--color-border-subtle)]"
          : "glow-card scenario-danger hover:bg-[rgba(255,77,103,0.05)] active:scale-[0.98]"
      }`}
    >
      <span className="text-[14px] font-semibold text-[var(--color-text-primary)]">
        {busy ? "Triggering…" : scenario.label}
      </span>
      <span className="text-[12px] leading-relaxed text-[var(--color-text-muted)]">{scenario.description}</span>
      <span className="font-mono text-[11px] text-[var(--color-text-faint)]">{scenario.service}</span>
    </button>
  );
}

function IncidentRow({
  incident,
  onOpen,
}: {
  incident: IncidentSummary;
  onOpen: () => void;
}) {
  const reveal = useScrollReveal<HTMLTableRowElement>();
  return (
    <tr
      ref={reveal.ref}
      style={reveal.style}
      className={`cursor-pointer border-b border-[var(--color-border-subtle)] transition-colors hover:bg-[var(--color-bg-hover)] ${reveal.className}`}
      onClick={onOpen}
    >
      <td className="py-2.5 pr-4">
        <Link
          href={`/incidents/${incident.id}`}
          className="font-mono text-[var(--color-accent-cyan)] hover:underline"
          onClick={(e) => e.stopPropagation()}
        >
          {incident.id.slice(0, 8)}…
        </Link>
      </td>
      <td className="py-2.5 pr-4 text-[var(--color-text-primary)]">{serviceDisplayName(incident.service_name)}</td>
      <td className="py-2.5 pr-4">
        <Chip tone={stateTone(incident.state)}>{incident.state}</Chip>
      </td>
      <td className="py-2.5 pr-4">
        {incident.severity ? <Chip tone={severityTone(incident.severity)}>{incident.severity}</Chip> : "—"}
      </td>
      <td className="py-2.5 pr-4 text-[var(--color-text-muted)]">{incident.current_stage ?? "—"}</td>
      <td className="py-2.5 text-[var(--color-text-muted)]">{new Date(incident.created_at).toLocaleTimeString()}</td>
    </tr>
  );
}

function KbResultCard({ result }: { result: KnowledgeBaseResult }) {
  const reveal = useScrollReveal<HTMLDivElement>();
  return (
    <div
      ref={reveal.ref}
      style={reveal.style}
      className={`glow-card bg-[var(--color-bg-surface)] px-4 py-3 ${reveal.className}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-[var(--color-text-primary)]">
          {serviceDisplayName(result.service)}
        </span>
        <MicroLabel>{(result.similarity * 100).toFixed(0)}% match</MicroLabel>
      </div>
      <p className="mt-1 text-[13px] text-[var(--color-text-secondary)]">{result.description}</p>
      <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
        root cause: {result.root_cause} · resolved via: {result.resolved_via}
      </p>
    </div>
  );
}

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

  const [targets, setTargets] = useState<FetchState<MonitoredTarget[]>>({
    status: "loading",
    data: null,
    error: null,
  });
  const [showAddTarget, setShowAddTarget] = useState(false);
  const [targetName, setTargetName] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [addTargetError, setAddTargetError] = useState<string | null>(null);
  const [addingTarget, setAddingTarget] = useState(false);
  // Per-target id -> which action is in flight, so only that row's buttons
  // disable rather than the whole section.
  const [targetActionPending, setTargetActionPending] = useState<Record<string, "toggle" | "delete">>({});
  const [targetActionError, setTargetActionError] = useState<Record<string, string>>({});

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

  const loadTargets = useCallback(async () => {
    try {
      const data = await listTargets();
      setTargets({ status: "ready", data, error: null });
    } catch (e) {
      setTargets({ status: "error", data: null, error: e instanceof ApiError ? e.message : "Unable to load monitored applications" });
    }
  }, []);

  useEffect(() => {
    loadHealth();
    loadIncidents();
    loadTargets();
    const interval = setInterval(() => {
      loadHealth();
      loadIncidents();
      loadTargets();
    }, 8000);
    return () => clearInterval(interval);
  }, [loadHealth, loadIncidents, loadTargets]);

  const handleAddTarget = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = targetName.trim();
    const url = targetUrl.trim();
    setAddTargetError(null);

    if (!name || !url) {
      setAddTargetError("Both a name and a URL are required.");
      return;
    }
    try {
      // eslint-disable-next-line no-new
      new URL(url);
    } catch {
      setAddTargetError("Enter a valid URL, including the scheme (e.g. https://example.com).");
      return;
    }

    setAddingTarget(true);
    try {
      await createTarget(name, url);
      setTargetName("");
      setTargetUrl("");
      setShowAddTarget(false);
      await loadTargets();
    } catch (e) {
      setAddTargetError(e instanceof ApiError ? e.message : "Unable to register this application");
    } finally {
      setAddingTarget(false);
    }
  };

  const handleToggleMonitoring = async (target: MonitoredTarget) => {
    setTargetActionPending((prev) => ({ ...prev, [target.id]: "toggle" }));
    setTargetActionError((prev) => {
      const next = { ...prev };
      delete next[target.id];
      return next;
    });
    try {
      await setTargetMonitoring(target.id, !target.monitoring_enabled);
      await loadTargets();
    } catch (e) {
      setTargetActionError((prev) => ({
        ...prev,
        [target.id]: e instanceof ApiError ? e.message : "Unable to update monitoring",
      }));
    } finally {
      setTargetActionPending((prev) => {
        const next = { ...prev };
        delete next[target.id];
        return next;
      });
    }
  };

  const handleDeleteTarget = async (target: MonitoredTarget) => {
    if (!window.confirm(`Stop monitoring "${target.name}" and remove it?`)) return;
    setTargetActionPending((prev) => ({ ...prev, [target.id]: "delete" }));
    setTargetActionError((prev) => {
      const next = { ...prev };
      delete next[target.id];
      return next;
    });
    try {
      await deleteTarget(target.id);
      await loadTargets();
    } catch (e) {
      setTargetActionError((prev) => ({
        ...prev,
        [target.id]: e instanceof ApiError ? e.message : "Unable to delete this application",
      }));
      setTargetActionPending((prev) => {
        const next = { ...prev };
        delete next[target.id];
        return next;
      });
    }
  };

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
              {KNOWN_SERVICES.map((svc, i) => (
                <ServiceHealthCard
                  key={svc}
                  svc={svc}
                  status={health.data?.[svc]}
                  loading={health.status === "loading"}
                  delay={i * 70}
                />
              ))}
            </div>
          )}
        </Panel>

        {/* ── trigger incident (demo/test scenarios — NOT real monitoring) ── */}
        <Panel title="Simulate Incident">
          <p className="-mt-1 mb-3 text-[12px] text-[var(--color-text-muted)]">
            Controlled demo scenarios against the managed services below — for testing the response
            pipeline, not real monitoring.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {SCENARIOS.map((s, i) => (
              <ScenarioButton
                key={s.id}
                scenario={s}
                busy={triggering === s.id}
                disabled={triggering !== null}
                delay={i * 70}
                onTrigger={() => handleTrigger(s.id, s.service)}
              />
            ))}
          </div>
          {triggerError && (
            <div className="mt-3 rounded-md border border-[rgba(255,77,103,0.25)] bg-[rgba(255,77,103,0.05)] px-4 py-3 text-[13px] text-[var(--color-accent-red)]">
              {triggerError}
            </div>
          )}
        </Panel>

        {/* ── monitored applications (real external URL monitoring) ── */}
        <Panel
          title="Monitored Applications"
          right={
            <div className="flex items-center gap-3">
              <button
                onClick={loadTargets}
                className="label-micro text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
              >
                Refresh
              </button>
              <Button variant="primary" size="sm" onClick={() => setShowAddTarget((v) => !v)}>
                {showAddTarget ? "Cancel" : "Add Application"}
              </Button>
            </div>
          }
        >
          <p className="-mt-1 mb-4 text-[12px] text-[var(--color-text-muted)]">
            Add an application URL and System Bachao will continuously watch it for failures — this is
            real monitoring against the address you provide, not a demo.
          </p>

          {showAddTarget && (
            <form
              onSubmit={handleAddTarget}
              className="mb-4 flex flex-col gap-3 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 sm:flex-row sm:items-end"
            >
              <div className="flex-1">
                <label className="label-micro mb-1 block text-[var(--color-text-muted)]">Application name</label>
                <input
                  type="text"
                  value={targetName}
                  onChange={(e) => setTargetName(e.target.value)}
                  placeholder="My API"
                  className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-base)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent-cyan)]"
                />
              </div>
              <div className="flex-1">
                <label className="label-micro mb-1 block text-[var(--color-text-muted)]">URL</label>
                <input
                  type="text"
                  value={targetUrl}
                  onChange={(e) => setTargetUrl(e.target.value)}
                  placeholder="https://example.com/health"
                  className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-base)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent-cyan)]"
                />
              </div>
              <Button variant="primary" size="md" disabled={addingTarget}>
                {addingTarget ? "Adding…" : "Start Monitoring"}
              </Button>
            </form>
          )}
          {addTargetError && (
            <div className="mb-4 rounded-md border border-[rgba(255,77,103,0.25)] bg-[rgba(255,77,103,0.05)] px-4 py-3 text-[13px] text-[var(--color-accent-red)]">
              {addTargetError}
            </div>
          )}

          {targets.status === "loading" ? (
            <p className="text-[13px] text-[var(--color-text-muted)]">Loading…</p>
          ) : targets.status === "error" ? (
            <div className="flex items-center justify-between gap-4 rounded-md border border-[rgba(255,77,103,0.25)] bg-[rgba(255,77,103,0.05)] px-4 py-3 text-[13px] text-[var(--color-accent-red)]">
              <span>{targets.error}</span>
              <button onClick={loadTargets} className="font-medium underline">
                Retry
              </button>
            </div>
          ) : targets.data && targets.data.length === 0 ? (
            <p className="text-[13px] text-[var(--color-text-muted)]">
              No applications registered yet. Add one above to start real monitoring.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {targets.data?.map((target) => {
                const pending = targetActionPending[target.id];
                const rowError = targetActionError[target.id];
                return (
                  <div
                    key={target.id}
                    className="rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)] px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <StatusDot state={targetHealthDot(target)} size="md" pulse={targetHealthDot(target) === "critical"} />
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-[14px] font-medium text-[var(--color-text-primary)]">{target.name}</span>
                            <Chip tone={target.monitoring_enabled ? "ok" : "neutral"}>
                              {target.monitoring_enabled ? "monitoring" : "paused"}
                            </Chip>
                          </div>
                          <a
                            href={target.url}
                            target="_blank"
                            rel="noreferrer"
                            className="truncate font-mono text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-accent-cyan)] hover:underline"
                          >
                            {target.url}
                          </a>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-4">
                        <div className="text-right">
                          <div className="font-mono text-[11px] text-[var(--color-text-muted)] capitalize">
                            {target.health_status}
                            {target.consecutive_failures > 0 && ` · ${target.consecutive_failures} failed check${target.consecutive_failures === 1 ? "" : "s"}`}
                          </div>
                          {target.last_checked_at && (
                            <div className="font-mono text-[10px] text-[var(--color-text-faint)]">
                              last checked {new Date(target.last_checked_at).toLocaleTimeString()}
                            </div>
                          )}
                        </div>

                        {(() => {
                          const incidentId = linkedIncidentId(target, incidents.data);
                          if (incidentId) {
                            return (
                              <Link href={`/incidents/${incidentId}`}>
                                <Button variant="danger" size="sm">
                                  View Incident
                                </Button>
                              </Link>
                            );
                          }
                          if (target.incident_reported) {
                            return <MicroLabel>incident reported</MicroLabel>;
                          }
                          return null;
                        })()}

                        <div className="flex items-center gap-2">
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={pending !== undefined}
                            onClick={() => handleToggleMonitoring(target)}
                          >
                            {pending === "toggle" ? "…" : target.monitoring_enabled ? "Pause" : "Resume"}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={pending !== undefined}
                            onClick={() => handleDeleteTarget(target)}
                          >
                            {pending === "delete" ? "…" : "Delete"}
                          </Button>
                        </div>
                      </div>
                    </div>
                    {rowError && <p className="mt-2 text-[12px] text-[var(--color-accent-red)]">{rowError}</p>}
                  </div>
                );
              })}
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
                    <IncidentRow key={inc.id} incident={inc} onOpen={() => router.push(`/incidents/${inc.id}`)} />
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
                  <KbResultCard key={r.id} result={r} />
                ))}
              </div>
            )
          )}
        </Panel>
      </main>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  createTarget,
  deleteTarget,
  listTargets,
  setTargetMonitoring,
} from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { Button, Chip, Panel, StatusDot } from "@/components/ui";
import { ConsoleShell } from "@/components/console/ConsoleShell";
import type { MonitoredTarget } from "@/lib/types";

const REFRESH_MS = 15000;

function targetDot(target: MonitoredTarget): "healthy" | "warning" | "critical" | "neutral" {
  if (!target.monitoring_enabled) return "neutral";
  if (target.health_status === "healthy") return "healthy";
  if (target.health_status === "unhealthy") return target.incident_reported ? "critical" : "warning";
  return "neutral";
}

function targetLabel(target: MonitoredTarget): string {
  if (!target.monitoring_enabled) return "Monitoring paused";
  if (target.health_status === "unknown") return "Awaiting first check";
  if (target.health_status === "healthy") return "Healthy";
  return target.incident_reported ? "Down" : "Degraded";
}

export default function MonitoringPage() {
  const [targets, setTargets] = useState<MonitoredTarget[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [pending, setPending] = useState<Record<string, "toggle" | "delete">>({});

  const load = useCallback(async () => {
    try {
      setTargets(await listTargets());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Unable to load monitored targets");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !url.trim()) {
      setAddError("Name and URL are required.");
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      await createTarget(name.trim(), url.trim());
      setName("");
      setUrl("");
      setShowAdd(false);
      await load();
    } catch (e) {
      setAddError(e instanceof ApiError ? e.message : "Failed to add target");
    } finally {
      setAdding(false);
    }
  };

  const handleToggle = async (target: MonitoredTarget) => {
    setPending((p) => ({ ...p, [target.id]: "toggle" }));
    try {
      await setTargetMonitoring(target.id, !target.monitoring_enabled);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to update target");
    } finally {
      setPending((p) => {
        const next = { ...p };
        delete next[target.id];
        return next;
      });
    }
  };

  const handleDelete = async (target: MonitoredTarget) => {
    setPending((p) => ({ ...p, [target.id]: "delete" }));
    try {
      await deleteTarget(target.id);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to delete target");
    } finally {
      setPending((p) => {
        const next = { ...p };
        delete next[target.id];
        return next;
      });
    }
  };

  return (
    <ConsoleShell title="Monitoring">
      <div className="space-y-6">
        {error && (
          <p className="rounded border border-[rgba(255,77,103,0.3)] bg-[rgba(255,77,103,0.07)] px-3 py-2 text-[12px] text-[var(--color-accent-red)]">
            {error}
          </p>
        )}

        <Panel
          title="Monitored Targets"
          right={
            <Button variant="secondary" size="sm" onClick={() => setShowAdd((v) => !v)}>
              {showAdd ? "Cancel" : "+ Add Target"}
            </Button>
          }
        >
          {showAdd && (
            <form onSubmit={handleAdd} className="mb-4 flex flex-col gap-2.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-base)] p-3 sm:flex-row sm:items-end">
              <div className="flex-1">
                <label htmlFor="target-name" className="label-micro mb-1 block text-[var(--color-text-muted)]">
                  Name
                </label>
                <input
                  id="target-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="billing-api"
                  className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none transition-colors focus:border-[var(--color-accent-cyan)]"
                />
              </div>
              <div className="flex-[2]">
                <label htmlFor="target-url" className="label-micro mb-1 block text-[var(--color-text-muted)]">
                  URL
                </label>
                <input
                  id="target-url"
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://api.example.com/health"
                  className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2 font-mono text-[12px] text-[var(--color-text-primary)] outline-none transition-colors focus:border-[var(--color-accent-cyan)]"
                />
              </div>
              <Button variant="primary" size="md" disabled={adding}>
                {adding ? "Adding…" : "Add"}
              </Button>
              {addError && <p className="w-full text-[12px] text-[var(--color-accent-red)]">{addError}</p>}
            </form>
          )}

          {!targets ? (
            <div className="grid place-items-center py-10">
              <div className="h-7 w-7 rounded-full border-2 border-[var(--color-border-default)] border-t-[var(--color-accent-cyan)] animate-spin" />
            </div>
          ) : targets.length === 0 ? (
            <p className="py-8 text-center text-[13px] text-[var(--color-text-muted)]">
              No targets registered. Add a URL above and the platform will health-check it —
              sustained failures open real incidents through the same pipeline.
            </p>
          ) : (
            <ul className="divide-y divide-[var(--color-border-subtle)]">
              {targets.map((t) => {
                const busy = pending[t.id];
                return (
                  <li key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-2 py-3">
                    <StatusDot state={targetDot(t)} size="sm" pulse={t.health_status === "unhealthy" && !t.incident_reported} />
                    <div className="min-w-[140px]">
                      <div className="text-[13px] font-medium text-[var(--color-text-primary)]">{t.name}</div>
                      <div className="max-w-[260px] truncate font-mono text-[10px] text-[var(--color-text-faint)]">{t.url}</div>
                    </div>
                    <Chip tone={t.health_status === "healthy" ? "ok" : t.incident_reported ? "crit" : t.health_status === "unhealthy" ? "warn" : "neutral"}>
                      {targetLabel(t)}
                    </Chip>
                    <span className="hidden font-mono text-[10px] text-[var(--color-text-faint)] md:inline">
                      {t.total_checks} checks · {t.avg_latency_ms != null ? `${t.avg_latency_ms.toFixed(0)}ms avg` : "—"}
                      {t.last_checked_at ? ` · ${timeAgo(t.last_checked_at)}` : ""}
                    </span>
                    <span className="ml-auto flex items-center gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={!!busy}
                        onClick={() => handleToggle(t)}
                      >
                        {busy === "toggle" ? "…" : t.monitoring_enabled ? "Pause" : "Resume"}
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        disabled={!!busy}
                        onClick={() => handleDelete(t)}
                      >
                        {busy === "delete" ? "…" : "Delete"}
                      </Button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
      </div>
    </ConsoleShell>
  );
}

"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, listIncidents } from "@/lib/api";
import { serviceDisplayName, timeAgo } from "@/lib/format";
import { Chip, Panel, StatusDot, type Tone } from "@/components/ui";
import { ConsoleShell } from "@/components/console/ConsoleShell";

const REFRESH_MS = 15000;

function stateTone(state: string): Tone {
  if (state === "resolved") return "ok";
  if (state === "failed" || state === "escalated") return "crit";
  if (state === "rejected") return "warn";
  return "info";
}

function severityTone(severity: string | null): Tone {
  if (severity === "P1") return "crit";
  if (severity === "P2") return "warn";
  return "neutral";
}

function incidentDot(state: string): "healthy" | "warning" | "critical" | "neutral" {
  if (state === "resolved") return "healthy";
  if (state === "failed" || state === "escalated") return "critical";
  if (state === "rejected") return "neutral";
  return "warning";
}

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Awaited<ReturnType<typeof listIncidents>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setIncidents(await listIncidents(100));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Unable to load incidents");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  return (
    <ConsoleShell title="Incidents">
      <Panel
        title="Incident History"
        right={
          <span className="font-mono text-[10px] tracking-[0.1em] text-[var(--color-text-faint)]">
            AUTO-REFRESH 15S
          </span>
        }
      >
        {error && (
          <p className="mb-3 rounded border border-[rgba(255,77,103,0.3)] bg-[rgba(255,77,103,0.07)] px-3 py-2 text-[12px] text-[var(--color-accent-red)]">
            {error}
          </p>
        )}
        {!incidents ? (
          <div className="grid place-items-center py-12">
            <div className="h-7 w-7 rounded-full border-2 border-[var(--color-border-default)] border-t-[var(--color-accent-cyan)] animate-spin" />
          </div>
        ) : incidents.length === 0 ? (
          <p className="py-10 text-center text-[13px] text-[var(--color-text-muted)]">
            No incidents yet — trigger a fault scenario from the Overview page.
          </p>
        ) : (
          <ul className="divide-y divide-[var(--color-border-subtle)]">
            {incidents.map((inc) => (
              <li key={inc.id}>
                <Link
                  href={`/incidents/${inc.id}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-2 py-3 transition-colors hover:bg-[var(--color-bg-hover)]"
                >
                  <StatusDot state={incidentDot(inc.state)} size="sm" pulse={inc.state !== "resolved" && inc.state !== "rejected"} />
                  <span className="min-w-[130px] text-[13px] font-medium text-[var(--color-text-primary)]">
                    {serviceDisplayName(inc.service_name)}
                  </span>
                  <span className="hidden font-mono text-[11px] text-[var(--color-text-muted)] sm:inline">
                    {inc.id.slice(0, 8)}…
                  </span>
                  <span className="flex items-center gap-1.5">
                    <Chip tone={inc.source === "simulator" ? "system" : "info"}>{inc.source === "simulator" ? "SIM" : "URL"}</Chip>
                    {inc.severity && <Chip tone={severityTone(inc.severity)}>{inc.severity}</Chip>}
                    <Chip tone={stateTone(inc.state)}>{inc.state.replace(/_/g, " ")}</Chip>
                  </span>
                  <span className="ml-auto font-mono text-[11px] text-[var(--color-text-faint)]">
                    {timeAgo(inc.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </ConsoleShell>
  );
}

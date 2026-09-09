"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, listIncidents } from "@/lib/api";
import { formatTime, serviceDisplayName, timeAgo } from "@/lib/format";
import { Chip, Panel, StatusDot, type Tone } from "@/components/ui";
import { ConsoleShell } from "@/components/console/ConsoleShell";
import type { IncidentSummary } from "@/lib/types";

const REFRESH_MS = 15000;

function activityTone(inc: IncidentSummary): Tone {
  if (inc.state === "resolved") return "ok";
  if (inc.state === "failed" || inc.state === "escalated") return "crit";
  if (inc.state === "rejected") return "neutral";
  return "info";
}

function activityMessage(inc: IncidentSummary): string {
  const svc = serviceDisplayName(inc.service_name);
  switch (inc.state) {
    case "resolved":
      return `Incident on ${svc} resolved and verified`;
    case "failed":
      return `Remediation on ${svc} failed — escalation required`;
    case "escalated":
      return `Incident on ${svc} escalated`;
    case "rejected":
      return `Remediation on ${svc} rejected by operator`;
    case "created":
    case "detected":
      return `Incident detected on ${svc}`;
    case "remediating":
      return `Executing remediation on ${svc}`;
    case "verifying":
      return `Verifying recovery on ${svc}`;
    default:
      return `Incident on ${svc}: ${inc.state.replace(/_/g, " ")}`;
  }
}

export default function ActivityPage() {
  const [incidents, setIncidents] = useState<IncidentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setIncidents(await listIncidents(100));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Unable to load activity");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  return (
    <ConsoleShell
      title="Activity"
      headerExtra={
        <span className="flex items-center gap-1.5">
          <StatusDot state="healthy" size="sm" pulse />
          <span className="font-mono text-[10px] tracking-[0.1em] text-[var(--color-text-muted)]">LIVE</span>
        </span>
      }
    >
      <Panel
        title="Operations Activity Feed"
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
            No activity yet — incident and detection events will appear here.
          </p>
        ) : (
          <ul>
            {incidents.map((inc) => (
              <li key={inc.id} className="relative flex gap-3 pb-5 last:pb-0">
                {/* timeline rail */}
                <div className="flex flex-col items-center">
                  <span className="mt-1">
                    <StatusDot
                      state={inc.state === "resolved" ? "healthy" : inc.state === "failed" || inc.state === "escalated" ? "critical" : "neutral"}
                      size="sm"
                    />
                  </span>
                  <span className="mt-1 w-px flex-1 bg-[var(--color-border-default)]" />
                </div>
                <div className="min-w-0 flex-1 pb-1">
                  <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                    <span className="font-mono text-[10px] tracking-[0.08em] text-[var(--color-text-faint)]">
                      {formatTime(inc.created_at)}
                    </span>
                    <Chip tone={activityTone(inc)}>{inc.state.replace(/_/g, " ")}</Chip>
                    <span className="font-mono text-[10px] text-[var(--color-text-faint)]">
                      {inc.source === "simulator" ? "SIMULATOR" : "URL MONITOR"}
                    </span>
                  </div>
                  <Link
                    href={`/incidents/${inc.id}`}
                    className="mt-1 block text-[13px] text-[var(--color-text-primary)] transition-colors hover:text-[var(--color-accent-cyan)]"
                  >
                    {activityMessage(inc)}
                  </Link>
                  <p className="mt-0.5 font-mono text-[10px] text-[var(--color-text-muted)]">
                    {inc.id.slice(0, 8)}… · {timeAgo(inc.created_at)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </ConsoleShell>
  );
}

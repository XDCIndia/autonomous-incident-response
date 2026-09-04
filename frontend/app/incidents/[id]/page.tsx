"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { LiveIncident } from "@/components/LiveIncident";
import { PostMortem } from "@/components/PostMortem";
import { canonicalReport, findIncident } from "@/lib/incidents";
import type { PostMortemReport } from "@/lib/types";

/* ── upstream API types (optional enrichment when backend is available) ── */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

interface TimelineEvent {
  id: string;
  timestamp: string;
  stage: string;
  status: string;
  message: string;
  metadata: Record<string, unknown>;
}

interface LogInvestigationResult {
  hypothesis: string;
  evidence: string[];
  confidence: number;
  suggested_root_cause: string;
}

interface MetricInvestigationResult {
  hypothesis: string;
  evidence: string[];
  confidence: number;
  suggested_root_cause: string;
  metrics_summary: Record<string, unknown>;
}

interface ArbiterResult {
  merged_hypothesis: string;
  root_cause: string;
  confidence: number;
  log_hypothesis_agrees: boolean;
  metric_hypothesis_agrees: boolean;
  conflict_description: string | null;
  evidence: string[];
  contributing_factors: string[];
}

interface ApiIncident {
  id: string;
  service_name: string;
  state: string;
  severity: string | null;
  current_stage: string | null;
  created_at: string;
  log_result: LogInvestigationResult | null;
  metric_result: MetricInvestigationResult | null;
  arbiter_result: ArbiterResult | null;
  report: {
    root_cause: string;
    impact: string;
    remediation_action: string;
    confidence: number;
    prevention: string;
    result_metrics: Record<string, { before: unknown; after: unknown }>;
  } | null;
}

interface SimilarIncident {
  id: number;
  incident_id: string | null;
  service: string;
  root_cause: string;
  description: string;
  resolved_via: string;
  created_at: string;
  similarity: number;
}

/** Optional enrichment passed to PostMortem when the real backend is reachable */
export interface ApiEnrichment {
  incident: ApiIncident | null;
  timeline: TimelineEvent[];
  connected: boolean;
  similarIncidents: SimilarIncident[] | null;
  similarLoading: boolean;
}

type View = "loading" | "live" | "report";

/**
 * Incident deep-dive.
 *  - /incidents/live            → the live command center (simulation entry point)
 *  - /incidents/<real id>       → stored post-mortem report, or the canonical demo report
 *
 * When NEXT_PUBLIC_API_URL points to a running backend, the page also
 * fetches the real incident record over REST + WebSocket and forwards that
 * data to the report view for display alongside the mock narrative.
 */
export default function IncidentPage() {
  const params = useParams();
  const id = (params.id as string) ?? "";
  const [view, setView] = useState<View>("loading");
  const [report, setReport] = useState<PostMortemReport | null>(null);
  const [fallback, setFallback] = useState(false);

  /* upstream: live API data (optional) */
  const [apiIncident, setApiIncident] = useState<ApiIncident | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [similarIncidents, setSimilarIncidents] = useState<SimilarIncident[] | null>(null);
  const [similarLoading, setSimilarLoading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  /* resolve which view to show */
  useEffect(() => {
    if (id === "live") {
      setView("live");
      return;
    }
    const found = findIncident(id);
    if (found) {
      setReport(found);
      setFallback(false);
      setView("report");
    } else {
      setReport(canonicalReport());
      setFallback(true);
      setView("report");
    }
  }, [id]);

  /* upstream: fetch real incident details when backend is available */
  useEffect(() => {
    if (id === "live" || !id) return;

    fetch(`${API_URL}/incidents/${id}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: ApiIncident) => setApiIncident(data))
      .catch(() => {
        /* backend not reachable — mock report already shown, nothing to do */
      });
  }, [id]);

  /* upstream: WebSocket for live timeline */
  useEffect(() => {
    if (id === "live" || !id) return;

    let ws: WebSocket;
    try {
      ws = new WebSocket(`${WS_URL}/ws/incidents/${id}`);
    } catch {
      return; // WS URL malformed — skip silently
    }
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "ping") return; // keepalive

        setTimeline((prev) =>
          prev.some((e) => e.id === data.id) ? prev : [...prev, data]
        );

        /* refresh incident details on new events */
        fetch(`${API_URL}/incidents/${id}`)
          .then((res) => res.json())
          .then((d: ApiIncident) => setApiIncident(d))
          .catch(() => {});
      } catch {
        /* non-JSON frame — ignore */
      }
    };
    ws.onclose = () => setConnected(false);

    return () => ws.close();
  }, [id]);

  /* upstream: knowledge-base similar-incident search once root cause is known */
  const reportRootCause = apiIncident?.report?.root_cause;
  useEffect(() => {
    if (!apiIncident || !reportRootCause) return;

    setSimilarLoading(true);
    const query = `${apiIncident.service_name} ${reportRootCause}`;
    fetch(`${API_URL}/knowledge-base/search?query=${encodeURIComponent(query)}&top_k=3`)
      .then((res) => res.json())
      .then((data) => setSimilarIncidents(data.results ?? []))
      .catch(() => setSimilarIncidents(null))
      .finally(() => setSimilarLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiIncident?.service_name, reportRootCause]);

  if (view === "live") {
    return <LiveIncident />;
  }

  if (view === "loading" || !report) {
    return (
      <div className="grid min-h-screen place-items-center bg-[var(--color-bg-base)]">
        <div className="text-center space-y-4">
          <div className="mx-auto h-8 w-8 rounded-full border-2 border-[var(--color-border-default)] border-t-[var(--color-accent-cyan)] animate-spin" />
          <p className="font-mono text-[11px] tracking-[0.14em] text-[var(--color-text-muted)]">
            loading incident report
          </p>
        </div>
      </div>
    );
  }

  const enrichment: ApiEnrichment = {
    incident: apiIncident,
    timeline,
    connected,
    similarIncidents,
    similarLoading,
  };

  return (
    <div className="min-h-screen">
      {/* slim brand bar */}
      <header className="sticky top-0 z-40 border-b border-[var(--color-border-subtle)] bg-[var(--color-bg-base)]/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1280px] items-center gap-4 px-4 py-2.5">
          <Link
            href="/"
            className="label-micro shrink-0 text-[var(--color-text-muted)] transition-colors duration-200 hover:text-[var(--color-text-primary)]"
          >
            ← OVERVIEW
          </Link>
          <div className="mx-1 hidden h-5 w-px bg-[var(--color-border-subtle)] sm:block" />
          <div className="relative grid h-8 w-8 shrink-0 place-items-center">
            <div className="absolute inset-0 rounded-full border border-[rgba(54,215,232,0.2)]" />
            <div className="absolute inset-[3px] rounded-full border border-dashed border-[rgba(255,77,103,0.25)]" />
            <div className="h-2.5 w-2.5 rounded-full bg-[var(--color-accent-red)] opacity-80" />
          </div>
          <div className="leading-tight">
            <span className="text-[14px] font-semibold tracking-[0.1em] text-[var(--color-text-primary)]">SYSTEM BACHAO</span>
            <span className="label-micro ml-3 text-[var(--color-text-faint)]">post-incident report</span>
          </div>
          {connected && (
            <span className="ml-2 hidden items-center gap-1.5 sm:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-status-healthy)]" />
              <span className="font-mono text-[10px] tracking-[0.1em] text-[var(--color-status-healthy)]">LIVE</span>
            </span>
          )}
          <span className="ml-auto font-mono text-[11px] tracking-[0.1em] text-[var(--color-text-muted)]">{report.id}</span>
        </div>
      </header>

      {fallback && (
        <div className="border-b border-[rgba(245,184,75,0.15)] bg-[rgba(245,184,75,0.03)]">
          <p className="mx-auto max-w-[1280px] px-4 py-2 font-mono text-[10px] tracking-[0.06em] text-[var(--color-accent-amber)] opacity-80">
            incident {id} was not simulated this session — showing the canonical demo incident {report.id}
          </p>
        </div>
      )}

      <PostMortem report={report} enrichment={enrichment} />
    </div>
  );
}

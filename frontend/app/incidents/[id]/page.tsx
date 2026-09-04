"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { LiveIncident } from "@/components/LiveIncident";
import { PostMortem } from "@/components/PostMortem";
import { canonicalReport, findIncident } from "@/lib/incidents";
import type { PostMortemReport } from "@/lib/types";

type View = "loading" | "live" | "report";

/**
 * Incident deep-dive.
 *  - /incidents/live            → the live command center (simulation entry point)
 *  - /incidents/<real id>       → stored post-mortem report, or the canonical demo report
 */
export default function IncidentPage() {
  const params = useParams();
  const id = (params.id as string) ?? "";
  const [view, setView] = useState<View>("loading");
  const [report, setReport] = useState<PostMortemReport | null>(null);
  const [fallback, setFallback] = useState(false);

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

      <PostMortem report={report} />
    </div>
  );
}

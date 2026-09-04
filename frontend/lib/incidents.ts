import { scenarioMap } from "./scenarios";
import type { PostMortemReport, PostMortemTimelineItem, Scenario } from "./types";

const STORAGE_KEY = "bachao.incidents.v1";
const PENDING_KEY = "bachao.pending-scenario.v1";

/* ------------------------------------------------------------------ *
 *  persistence (session only — demo data)
 * ------------------------------------------------------------------ */

export function persistIncident(report: PostMortemReport): void {
  if (typeof window === "undefined") return;
  try {
    const all = loadStored();
    const next = [report, ...all.filter((r) => r.id !== report.id)].slice(0, 20);
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* private mode etc. — ignore */
  }
}

export function loadStored(): PostMortemReport[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as PostMortemReport[]) : [];
  } catch {
    return [];
  }
}

export function findIncident(id: string): PostMortemReport | null {
  const found = loadStored().find((r) => r.id === id);
  return found ?? null;
}

/* ------------------------------------------------------------------ *
 *  pending scenario handoff (home → live incident view)
 * ------------------------------------------------------------------ */

export function setPendingScenario(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      PENDING_KEY,
      JSON.stringify({ scenarioKey: key, ts: Date.now() })
    );
  } catch {
    /* private mode — ignore */
  }
}

/** Read + clear the pending scenario (consumed once by the live view). */
export function takePendingScenario(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    window.sessionStorage.removeItem(PENDING_KEY);
    const parsed = JSON.parse(raw) as { scenarioKey?: unknown };
    return typeof parsed?.scenarioKey === "string" ? parsed.scenarioKey : null;
  } catch {
    return null;
  }
}

/** node key → human label, e.g. payment-service → Payment Service */
export function serviceDisplayName(key: string): string {
  if (!key) return key;
  return key
    .split("-")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/* ------------------------------------------------------------------ *
 *  ids + clock labels
 * ------------------------------------------------------------------ */

export function nextIncidentId(existing: number): string {
  const d = new Date();
  const ymd = [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, "0"),
    String(d.getDate()).padStart(2, "0"),
  ].join("");
  return `INC-${ymd}-${String(existing + 1).padStart(2, "0")}`;
}

export function clockLabel(d = new Date()): string {
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

/* ------------------------------------------------------------------ *
 *  canonical demo incident (used by the deep-link post-mortem page
 *  when no live simulation has been run this session)
 * ------------------------------------------------------------------ */

const CANONICAL_START = "2026-09-04T07:42:09.000Z";
const CANONICAL_END = "2026-09-04T07:53:41.000Z";
const CANONICAL_ID = "INC-20260904-01";

export function canonicalReport(): PostMortemReport {
  const scenario = scenarioMap["bad_deployment"];
  const timeline = scenarioTimeline(scenario);
  return buildReport(scenario, {
    id: CANONICAL_ID,
    startedAt: CANONICAL_START,
    resolvedAt: CANONICAL_END,
    durationSec: Math.round(
      (new Date(CANONICAL_END).getTime() - new Date(CANONICAL_START).getTime()) /
        1000
    ),
    timeline,
  });
}

/** Standard narration lines for a scenario, used to seed canonical timelines */
export function scenarioTimeline(scenario: Scenario): PostMortemTimelineItem[] {
  const blast = blastServices(scenario);
  const items: Omit<PostMortemTimelineItem, "t">[] = [
    {
      stage: "DETECT",
      msg: `Anomaly flagged on ${scenario.failedNode} — ${scenario.detectSignals[0]?.metric} ${scenario.detectSignals[0]?.value}`,
      tone: "crit",
    },
    { stage: "DETECT", msg: `${scenario.severity} alert opened: ${scenario.headline}`, tone: "warn" },
    {
      stage: "TRIAGE",
      msg: `Blast radius mapped — ${blast.join(", ")}`,
      tone: "warn",
    },
    {
      stage: "INVESTIGATE",
      msg: "Log + metric investigators dispatched in parallel",
      tone: "info",
    },
    ...scenario.findings.map((f) => ({
      stage: "INVESTIGATE" as const,
      msg: `${f.agent}: ${f.headline}`,
      tone: "info" as const,
    })),
    {
      stage: "RCA",
      msg: `Root cause isolated — ${scenario.rca.headline} · confidence ${scenario.rca.confidencePct}%`,
      tone: "ok",
    },
    {
      stage: "REMEDIATE",
      msg: `${scenario.remediation.action} on ${scenario.remediation.target} approved (${scenario.remediation.risk} risk)`,
      tone: "warn",
    },
    {
      stage: "VERIFY",
      msg: `All ${scenario.verifyChecks.length} verification checks passed`,
      tone: "ok",
    },
    {
      stage: "RESOLVED",
      msg: "Incident resolved — telemetry nominal, AI core re-armed",
      tone: "ok",
    },
  ];

  // stamp items across the incident duration
  return items.map((item, i) => {
    const sec = Math.round(12 + i * 60);
    return { ...item, t: stampOffset(sec) };
  });
}

function stampOffset(sec: number): string {
  const base = new Date(CANONICAL_START);
  base.setSeconds(base.getSeconds() + sec);
  return clockLabel(base);
}

function blastServices(scenario: Scenario): string[] {
  const names: string[] = [];
  for (const beat of scenario.spread) {
    for (const key of Object.keys(beat.set ?? {})) {
      if (!names.includes(key)) names.push(key);
    }
  }
  return names;
}

/* ------------------------------------------------------------------ *
 *  report builder (shared by the live engine + canonical seed)
 * ------------------------------------------------------------------ */

export function buildReport(
  scenario: Scenario,
  opts: {
    id: string;
    startedAt: string;
    resolvedAt: string;
    durationSec: number;
    timeline: PostMortemTimelineItem[];
  }
): PostMortemReport {
  return {
    id: opts.id,
    scenarioKey: scenario.key,
    severity: scenario.severity,
    autonomy: scenario.autonomy,
    serviceName: scenario.failedNode,
    headline: scenario.headline,
    startedAt: opts.startedAt,
    resolvedAt: opts.resolvedAt,
    durationSec: opts.durationSec,
    rootCauseNode: scenario.rcNode,
    rootCause: scenario.report.rootCause,
    impact: scenario.report.impact,
    actionTaken: scenario.report.actionTaken,
    risk: scenario.remediation.risk,
    confidencePct: scenario.rca.confidencePct,
    prevention: scenario.report.prevention,
    metrics: scenario.report.metrics,
    checks: scenario.verifyChecks,
    timeline: opts.timeline,
  };
}

export function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

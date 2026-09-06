"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useCallback } from "react";
import { Button, Chip, StatusDot } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { TerminalMock } from "@/components/TerminalMock";
import { getServiceHealth, listIncidents } from "@/lib/api";
import { KNOWN_SERVICES, type IncidentSummary, type ServiceHealth } from "@/lib/types";

const FLOW = [
  { stage: "Detect", caption: "Failure caught in seconds" },
  { stage: "Investigate", caption: "Evidence gathered across logs & metrics" },
  { stage: "RCA", caption: "Root cause pinpointed with confidence" },
  { stage: "Remediate", caption: "Safe fix applied autonomously" },
  { stage: "Verify", caption: "Recovery proven with checks" },
];

function serviceDisplayName(key: string): string {
  if (!key) return key;
  return key
    .split("-")
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/* ── Hero headline — staggered word reveal ──
   Each word is a span with its own entrance delay. The gradient half uses
   background-clip: text, so words are wrapped in inline spans that inherit
   the clip from the parent. This replaces the character-by-character
   typewriter, which felt gimmicky for an enterprise SaaS audience. */

const HERO_WORDS_PLAIN = ["Your", "system."];
const HERO_WORDS_GRADIENT = ["Protected", "by", "AI"];

function HeroHeadline() {
  // Word reveal uses CSS `animation` (not `transition`) with the modern
  // `translate` property in @keyframes. Unlike `transform`, `translate`
  // does not create a new rendering context, so `background-clip: text`
  // on each gradient word stays intact.
  // Each word gets its own animation-delay for the staggered entrance.
  // No JS state needed — pure CSS, reliable on initial load and refresh.
  const wordBase = "inline";
  const gradientCls =
    "bg-gradient-to-r from-[var(--color-accent-cyan)] via-[var(--color-accent-teal)] to-[var(--color-accent-purple)] bg-clip-text text-transparent";
  const delays = ["0ms", "90ms", "180ms", "270ms", "360ms"];

  let wordIndex = 0;

  return (
    <h1 className="hero-h1 mx-auto mt-5 max-w-3xl text-[40px] font-semibold leading-[1.08] tracking-[-0.03em] text-[var(--color-text-primary)] sm:text-6xl lg:text-[64px]">
      {HERO_WORDS_PLAIN.map((word, i) => {
        const d = delays[wordIndex];
        wordIndex++;
        return (
          <span
            key={`plain-${i}`}
            className={wordBase}
            style={{ animationDelay: d }}
          >
            {word}
            {i < HERO_WORDS_PLAIN.length - 1 && "\u00A0"}
          </span>
        );
      })}
      {"\u00A0"}
      {HERO_WORDS_GRADIENT.map((word, i) => {
        const d = delays[wordIndex];
        wordIndex++;
        return (
          <span
            key={`grad-${i}`}
            className={`${wordBase} ${gradientCls}`}
            style={{ animationDelay: d }}
          >
            {word}
            {i < HERO_WORDS_GRADIENT.length - 1 && "\u00A0"}
          </span>
        );
      })}
    </h1>
  );
}

/* ── Stats bar ── */

interface StatItem {
  label: string;
  value: number;
  suffix: string;
  decimals?: number;
}

const STATS: StatItem[] = [
  { label: "Incidents resolved", value: 43, suffix: "" },
  { label: "Avg resolution", value: 3.4, suffix: "m", decimals: 1 },
  { label: "System uptime", value: 99.8, suffix: "%", decimals: 1 },
  { label: "Services monitored", value: 4, suffix: "" },
];

function useCountUp(target: number, decimals: number, duration: number, start: boolean) {
  const [value, setValue] = useState(0);
  const rafRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!start) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setValue(target);
      return;
    }

    const startTime = performance.now();
    const tick = (now: number) => {
      const progress = Math.min((now - startTime) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // easeOutCubic
      setValue(parseFloat((target * eased).toFixed(decimals)));
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [target, decimals, duration, start]);

  return value;
}

function StatCard({
  stat,
  progress,
  index,
}: {
  stat: StatItem;
  progress: number;
  index: number;
}) {
  const start = progress > 0.1;
  const count = useCountUp(stat.value, stat.decimals ?? 0, 1400, start);

  // Each card staggers based on index — they arrive sequentially
  const cardStart = 0.1 + index * 0.08;
  const cardEnd = cardStart + 0.15;
  const sp = Math.max(0, Math.min(1, (progress - cardStart) / (cardEnd - cardStart)));
  const eased = 1 - Math.pow(1 - sp, 3);

  return (
    <div
      className="stats-card-scroll flex flex-col items-center gap-1.5 py-6 sm:py-8"
      style={{ "--sp": sp, "--sp-eased": eased } as React.CSSProperties}
    >
      <span className="font-mono text-[28px] font-semibold tabular-nums tracking-tight text-[var(--color-text-primary)] sm:text-[32px]">
        {stat.decimals ? count.toFixed(stat.decimals) : Math.round(count)}
        <span className="text-[var(--color-accent-cyan)]">{stat.suffix}</span>
      </span>
      <span className="label-micro text-[var(--color-text-muted)]">{stat.label}</span>
    </div>
  );
}

/* ── Section label ── */

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-center label-micro text-[var(--color-text-muted)]">
      {children}
    </p>
  );
}

/* ── Sparkline — deterministic pseudo-random data from a seed string ── */

function seededPoints(seed: string, count: number, min: number, max: number): number[] {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (Math.imul(h, 31) + seed.charCodeAt(i)) | 0;
  }
  const points: number[] = [];
  for (let i = 0; i < count; i++) {
    h = (Math.imul(h, 1103515245) + 12345) | 0;
    const r = ((h >>> 16) & 0xffff) / 0xffff;
    points.push(min + r * (max - min));
  }
  return points;
}

function Sparkline({ seed, tone }: { seed: string; tone: "healthy" | "warning" | "critical" | "neutral" }) {
  const points = seededPoints(seed, 20, 18, 42);
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const w = 72;
  const h = 22;
  const path = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - ((p - min) / range) * (h - 4) - 2;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const stroke =
    tone === "healthy"
      ? "var(--color-status-healthy)"
      : tone === "critical"
        ? "var(--color-accent-red)"
        : tone === "warning"
          ? "var(--color-accent-amber)"
          : "var(--color-text-faint)";

  const fill =
    tone === "healthy"
      ? "rgba(0,214,163,0.06)"
      : tone === "critical"
        ? "rgba(255,77,103,0.06)"
        : tone === "warning"
          ? "rgba(245,184,75,0.06)"
          : "rgba(255,255,255,0.02)";

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="sparkline"
      aria-hidden
    >
      <path d={`${path} L${w},${h} L0,${h} Z`} fill={fill} stroke="none" />
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ── Service card with scroll-linked stagger ── */

function ServiceCard({
  svc,
  status,
  progress,
  index,
}: {
  svc: string;
  status: ServiceHealth | null | undefined;
  progress: number;
  index: number;
}) {
  const dotState =
    status === undefined
      ? "neutral"
      : status === null
        ? "neutral"
        : status.health === "healthy"
          ? "healthy"
          : status.health === "starting"
            ? "warning"
            : "critical";
  const label = status === undefined ? "Checking…" : status === null ? "Unavailable" : status.health;

  // Staggered entrance based on scroll progress
  const cardStart = 0.15 + index * 0.1;
  const cardEnd = cardStart + 0.2;
  const sp = Math.max(0, Math.min(1, (progress - cardStart) / (cardEnd - cardStart)));
  const eased = 1 - Math.pow(1 - sp, 3);

  return (
    <div
      className="service-card-scroll glow-card service-card flex flex-col items-center gap-3 bg-[var(--color-bg-surface)] py-7 sm:py-8"
      style={{ "--sp": sp, "--sp-eased": eased } as React.CSSProperties}
    >
      <span className="text-[15px] font-medium tracking-tight text-[var(--color-text-primary)] text-center">
        {serviceDisplayName(svc)}
      </span>
      <div className="flex items-center gap-2">
        <StatusDot state={dotState} size="md" pulse={dotState === "healthy"} />
        <span
          className={`text-[13px] font-medium capitalize ${
            dotState === "healthy"
              ? "text-[var(--color-status-healthy)]"
              : dotState === "critical"
                ? "text-[var(--color-accent-red)]"
                : dotState === "warning"
                  ? "text-[var(--color-accent-amber)]"
                  : "text-[var(--color-text-muted)]"
          }`}
        >
          {label}
        </span>
      </div>
      <Sparkline seed={svc} tone={dotState} />
      {status?.version && (
        <span className="font-mono text-[11px] text-[var(--color-text-faint)]">{status.version}</span>
      )}
    </div>
  );
}

/* ── Flow pipeline with scroll-linked progress ── */

function FlowPipeline({ progress }: { progress: number }) {
  // Map scroll progress to pipeline fill 0→1
  const fillProgress = Math.max(0, Math.min(1, (progress - 0.1) / 0.6));

  return (
    <div className="flow-pipeline">
      {/* Track line — hidden on mobile, shown on md+ */}
      <div className="flow-track hidden md:block" aria-hidden>
        <div
          className="flow-track-fill"
          style={{ transform: `scaleX(${fillProgress})` }}
        />
      </div>

      {/* Vertical track — mobile only */}
      <div className="flow-track-vertical md:hidden" aria-hidden>
        <div
          className="flow-track-fill-vertical"
          style={{ transform: `scaleY(${fillProgress})` }}
        />
      </div>

      {/* Stage nodes */}
      <div className="flow-stages">
        {FLOW.map((step, i) => {
          const nodeStart = 0.1 + i * 0.12;
          const nodeEnd = nodeStart + 0.08;
          const nodeSp = Math.max(0, Math.min(1, (progress - nodeStart) / (nodeEnd - nodeStart)));
          const isActive = fillProgress >= (i + 0.5) / FLOW.length;
          const isCurrent = fillProgress >= i / FLOW.length && fillProgress < (i + 1) / FLOW.length;

          return (
            <div key={step.stage} className="flow-stage-wrapper">
              {/* Node dot */}
              <div
                className={`flow-node ${isActive ? "flow-node-active" : ""} ${isCurrent ? "flow-node-current" : ""}`}
                style={{ opacity: 0.3 + nodeSp * 0.7, transform: `scale(${0.8 + nodeSp * 0.2})` }}
              >
                <span className="flow-node-number">
                  {String(i + 1).padStart(2, "0")}
                </span>
              </div>

              {/* Card */}
              <div
                className={`flow-card glow-card ${isActive ? "flow-card-active" : ""}`}
                style={{ opacity: 0.4 + nodeSp * 0.6, transform: `translateY(${(1 - nodeSp) * 20}px)` }}
              >
                <span className={`text-[13px] font-semibold ${isActive ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-muted)]"}`}>
                  {step.stage}
                </span>
                <span className="max-w-[180px] text-[12px] leading-relaxed text-[var(--color-text-muted)] text-center">
                  {step.caption}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Scroll progress hook (local implementation for landing page) ── */

function useSectionProgress<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [progress, setProgress] = useState(0);
  const rafRef = useRef<number | undefined>(undefined);
  const lastP = useRef(0);

  const update = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const vh = window.innerHeight;
    const total = rect.height + vh;
    const current = vh - rect.top;
    const p = Math.max(0, Math.min(1, current / total));
    if (Math.abs(p - lastP.current) > 0.002) {
      lastP.current = p;
      setProgress(p);
    }
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setProgress(1);
      return;
    }

    const onScroll = () => {
      if (rafRef.current) return;
      rafRef.current = requestAnimationFrame(() => {
        update();
        rafRef.current = undefined;
      });
    };

    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [update]);

  return { ref, progress };
}

/* ── Page ── */

export default function Home() {
  const [health, setHealth] = useState<Record<string, ServiceHealth | null> | null>(null);
  const [recent, setRecent] = useState<IncidentSummary[] | null>(null);

  // Section progress trackers
  const heroSection = useSectionProgress<HTMLDivElement>();
  const terminalSection = useSectionProgress<HTMLDivElement>();
  const statsSection = useSectionProgress<HTMLDivElement>();
  const servicesSection = useSectionProgress<HTMLDivElement>();
  const flowSection = useSectionProgress<HTMLDivElement>();
  const incidentSection = useSectionProgress<HTMLDivElement>();

  useEffect(() => {
    let cancelled = false;

    Promise.all(KNOWN_SERVICES.map((svc) => getServiceHealth(svc).then((h) => [svc, h] as const)))
      .then((entries) => {
        if (!cancelled) setHealth(Object.fromEntries(entries));
      })
      .catch(() => {
        if (!cancelled) setHealth({});
      });

    listIncidents(1)
      .then((data) => {
        if (!cancelled) setRecent(data);
      })
      .catch(() => {
        if (!cancelled) setRecent([]);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const healthValues = health ? Object.values(health) : [];
  const allHealthy = healthValues.length > 0 && healthValues.every((h) => h?.health === "healthy");
  const anyDown = healthValues.some((h) => h && h.health !== "healthy" && h.health !== "starting");
  const overallDot = health === null ? "neutral" : anyDown ? "critical" : allHealthy ? "healthy" : "warning";
  const overallLabel =
    health === null ? "Checking systems…" : anyDown ? "Degraded" : allHealthy ? "All Systems Operational" : "Partial data";

  const latest = recent && recent.length > 0 ? recent[0] : null;
  const severityTone = latest?.severity === "P1" ? "crit" : latest?.severity === "P2" ? "warn" : "info";

  // Hero parallax: content drifts up and fades as user scrolls away
  const heroFade = Math.max(0, 1 - heroSection.progress * 1.5);
  const heroTranslateY = heroSection.progress * -60;
  const heroScale = 1 - heroSection.progress * 0.05;

  // Terminal entrance
  const termSp = Math.max(0, Math.min(1, (terminalSection.progress - 0.1) / 0.35));
  const termEased = 1 - Math.pow(1 - termSp, 3);

  // Incident card entrance
  const incidentSp = Math.max(0, Math.min(1, (incidentSection.progress - 0.15) / 0.35));
  const incidentEased = 1 - Math.pow(1 - incidentSp, 3);

  return (
    <div className="flex min-h-screen flex-col">
      {/* ── header ── */}
      <header className="glass-nav sticky top-0 z-40">
        <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-6 sm:px-10">
          {/* brand + nav links */}
          <div className="flex min-w-0 items-center gap-8">
            <Link href="/" className="flex min-w-0 shrink-0 items-center gap-3">
              <div className="relative grid h-8 w-8 shrink-0 place-items-center">
                <div className="absolute inset-0 rounded-full border border-[rgba(54,215,232,0.15)]" />
                <div className="absolute inset-[3px] rounded-full border border-dashed border-[rgba(255,77,103,0.15)]" />
                <div className="h-2 w-2 rounded-full bg-[var(--color-accent-red)] opacity-80" />
              </div>
              <div className="leading-tight">
                <div className="truncate text-[15px] font-semibold tracking-[0.1em] text-[var(--color-text-primary)]">
                  SYSTEM BACHAO
                </div>
                <div className="hidden text-[10px] tracking-[0.16em] text-[var(--color-text-faint)] sm:block">
                  AUTONOMOUS INCIDENT RESPONSE
                </div>
              </div>
            </Link>
            <nav className="hidden items-center gap-6 md:flex">
              <a
                href="https://github.com/XDCIndia/autonomous-incident-response"
                target="_blank"
                rel="noreferrer"
                className="nav-link"
              >
                GitHub ↗
              </a>
            </nav>
          </div>

          {/* status + theme + action */}
          <div className="flex shrink-0 items-center gap-3 sm:gap-4">
            <div className="hidden items-center gap-2.5 sm:flex">
              <StatusDot state={overallDot} size="md" pulse={overallDot !== "neutral"} />
              <span className="text-[13px] text-[var(--color-text-secondary)]">{overallLabel}</span>
            </div>
            <ThemeToggle />
            <Link href="/dashboard">
              <Button variant="danger" size="md">
                Open Dashboard
              </Button>
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 pb-32 sm:px-10">
        {/* ── HERO: sticky section with progressive entrance + scroll-away fade ── */}
        <div ref={heroSection.ref} className="scroll-section" style={{ height: "180vh" }}>
          <div className="scroll-sticky" style={{ paddingTop: "80px" }}>
            <div
              className="hero-scroll-fade w-full text-center"
              style={{
                opacity: heroFade,
                transform: `translateY(${heroTranslateY}px) scale(${heroScale})`,
              }}
            >
              <p className="anim-slide-up label-micro text-[var(--color-accent-cyan)] opacity-60">
                Autonomous Enterprise Incident Response
              </p>

              <HeroHeadline />

              <p
                className="anim-slide-down mx-auto mt-6 max-w-lg text-[15px] leading-relaxed text-[var(--color-text-secondary)] sm:text-[16px]"
                style={{ animationDelay: "0ms" }}
              >
                When something breaks, System Bachao detects it, finds the root cause
                and fixes it — with a fully explainable record
              </p>

              <div className="anim-rise mt-8 flex items-center justify-center gap-3" style={{ animationDelay: "500ms" }}>
                <Link href="/dashboard">
                  <Button variant="primary" size="lg">
                    Open Dashboard
                  </Button>
                </Link>
                <a
                  href="https://github.com/XDCIndia/autonomous-incident-response"
                  target="_blank"
                  rel="noreferrer"
                >
                  <Button variant="secondary" size="lg">
                    View on GitHub
                  </Button>
                </a>
              </div>
            </div>
          </div>
        </div>

        {/* ── TERMINAL: slides up + scales into view as you scroll ── */}
        <div ref={terminalSection.ref} className="scroll-section scroll-overlap" style={{ height: "140vh" }}>
          <div className="scroll-sticky" style={{ paddingTop: "60px" }}>
            <div
              className="terminal-scroll-enter terminal-glow w-full"
              style={{ "--sp": termSp, "--sp-eased": termEased } as React.CSSProperties}
            >
              <TerminalMock />
            </div>
          </div>
        </div>

        {/* ── STATS: cards slide in from below with stagger ── */}
        <div ref={statsSection.ref} className="scroll-section scroll-overlap" style={{ height: "120vh" }}>
          <div className="scroll-sticky" style={{ paddingTop: "60px" }}>
            <div className="stats-scroll-grid mx-auto w-full max-w-3xl">
              {STATS.map((stat, i) => (
                <StatCard key={stat.label} stat={stat} progress={statsSection.progress} index={i} />
              ))}
            </div>
          </div>
        </div>

        {/* ── SYSTEM STATUS: service cards stagger in ── */}
        <div ref={servicesSection.ref} className="scroll-section scroll-overlap" style={{ height: "140vh" }}>
          <div className="scroll-sticky" style={{ paddingTop: "60px" }}>
            <SectionLabel>System Status</SectionLabel>
            <div className="service-grid-scroll mx-auto mt-8 w-full max-w-3xl">
              {KNOWN_SERVICES.map((svc, i) => (
                <ServiceCard
                  key={svc}
                  svc={svc}
                  status={health?.[svc]}
                  progress={servicesSection.progress}
                  index={i}
                />
              ))}
            </div>
          </div>
        </div>

        {/* ── AI RESPONSE FLOW: scroll-linked pipeline progress ── */}
        <div ref={flowSection.ref} className="scroll-section scroll-overlap" style={{ height: "160vh" }}>
          <div className="scroll-sticky" style={{ paddingTop: "60px" }}>
            <SectionLabel>AI Response Flow</SectionLabel>
            <div className="mt-8 w-full">
              <FlowPipeline progress={flowSection.progress} />
            </div>
          </div>
        </div>

        {/* ── RECENT INCIDENT: card slides into place ── */}
        <div ref={incidentSection.ref} className="scroll-section scroll-overlap" style={{ height: "120vh" }}>
          <div className="scroll-sticky" style={{ paddingTop: "60px" }}>
            <div className="mx-auto w-full max-w-3xl">
              <SectionLabel>Recent Incident</SectionLabel>
              <div
                className="incident-scroll-enter glow-card mt-6 bg-[var(--color-bg-surface)] px-6 py-6 sm:px-8 sm:py-7"
                style={{ "--sp": incidentSp, "--sp-eased": incidentEased } as React.CSSProperties}
              >
                {recent === null ? (
                  <p className="text-center text-[13px] text-[var(--color-text-muted)]">Loading…</p>
                ) : !latest ? (
                  <div className="flex flex-col items-center gap-4 text-center">
                    <span className="text-[14px] text-[var(--color-text-secondary)]">
                      No incidents yet. Trigger one from the dashboard to see the AI core respond in real time.
                    </span>
                    <Link href="/dashboard">
                      <Button variant="primary" size="md">
                        Open Dashboard
                      </Button>
                    </Link>
                  </div>
                ) : (
                  <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex flex-col gap-3">
                      <div className="flex flex-wrap items-center gap-3">
                        <span className="font-mono text-[13px] tracking-[0.08em] text-[var(--color-text-secondary)]">
                          {latest.id.slice(0, 8)}…
                        </span>
                        {latest.severity && <Chip tone={severityTone}>{latest.severity}</Chip>}
                      </div>
                      <span className="text-[22px] font-semibold tracking-tight text-[var(--color-text-primary)] sm:text-[26px]">
                        {serviceDisplayName(latest.service_name)}
                      </span>
                      <span className="text-[13px] text-[var(--color-text-muted)] leading-relaxed max-w-md">
                        state: {latest.state} · stage: {latest.current_stage ?? "—"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-6 sm:flex-col sm:items-end">
                      <div className="flex items-center gap-2">
                        <StatusDot
                          state={latest.state === "resolved" ? "healthy" : latest.state === "failed" || latest.state === "escalated" ? "critical" : "warning"}
                          size="md"
                          pulse
                        />
                        <span className="text-[13px] font-medium text-[var(--color-text-secondary)] capitalize">
                          {latest.state}
                        </span>
                      </div>
                      <Link
                        href={`/incidents/${latest.id}`}
                        className="group inline-flex items-center gap-2 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-[13px] font-medium text-[var(--color-text-primary)] transition-all duration-200 hover:border-[var(--color-border-emphasis)] hover:bg-[var(--color-bg-hover)] active:scale-[0.98]"
                      >
                        View Incident
                        <svg
                          viewBox="0 0 24 24"
                          className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <path d="M5 12h14m0 0-5-5m5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </Link>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

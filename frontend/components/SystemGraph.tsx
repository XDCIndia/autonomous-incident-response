"use client";

import { useMemo, useState } from "react";
import { EDGES, NODES, dependentsOf, nodeMap } from "@/lib/scenarios";
import type { Health } from "@/lib/types";
import { Chip } from "./ui";
import { IconCheck } from "./icons";

const W = 1040;
const H = 545;
const R = 26;

const HEALTH_TEXT: Record<Health, string> = {
  healthy: "",
  degraded: "DEGRADED",
  down: "DOWN",
};

const ROLE_TAG: Record<string, string> = {
  edge: "EDGE",
  service: "SERVICE",
  data: "DATA · PG15",
  external: "EXTERNAL",
};

/* theme-aware SVG colors via CSS custom properties */
const SVG = {
  nodeFill: "var(--svg-node-fill)",
  edgeDefault: "var(--svg-edge-default)",
  edgeExternal: "var(--svg-edge-external)",
  edgeAlert: "var(--svg-edge-alert)",
  textLabel: "var(--svg-text-label)",
  textLabelDown: "var(--svg-text-label-down)",
  textRole: "var(--svg-text-role)",
  backdrop: "var(--svg-backdrop)",
  pulseFill: "var(--svg-pulse-fill)",
} as const;

interface LineGeom {
  d: string;
  dx: number; // chord angle
  bx: number;
  by: number;
}

function chord(a: { x: number; y: number }, b: { x: number; y: number }) {
  return Math.atan2(b.y - a.y, b.x - a.x);
}

/** quadratic path between node boundaries + arrow tip position */
function lineGeom(from: string, to: string): LineGeom {
  const A = nodeMap[from];
  const B = nodeMap[to];
  const ang = chord(A, B);
  const ux = Math.cos(ang);
  const uy = Math.sin(ang);
  const ax = A.x + ux * (R + 3);
  const ay = A.y + uy * (R + 3);
  const bx = B.x - ux * (R + 9);
  const by = B.y - uy * (R + 9);
  // bow the line slightly for a hand-routed feel
  const mx = (A.x + B.x) / 2;
  const my = (A.y + B.y) / 2;
  const px = -uy;
  const py = ux;
  const bow = ((from.charCodeAt(0) + to.charCodeAt(0)) % 2 === 0 ? 1 : -1) * 16;
  const d = `M ${ax.toFixed(1)} ${ay.toFixed(1)} Q ${(mx + px * bow).toFixed(1)} ${(
    my + py * bow
  ).toFixed(1)} ${bx.toFixed(1)} ${by.toFixed(1)}`;
  return { d, dx: ang, bx, by };
}

function arrowPoints(g: LineGeom, size = 5.5) {
  const tipX = g.bx;
  const tipY = g.by;
  const a1 = g.dx + 0.55;
  const a2 = g.dx - 0.55;
  const p1x = tipX - Math.cos(a1) * size;
  const p1y = tipY - Math.sin(a1) * size;
  const p2x = tipX - Math.cos(a2) * size;
  const p2y = tipY - Math.sin(a2) * size;
  return `${tipX.toFixed(1)},${tipY.toFixed(1)} ${p1x.toFixed(1)},${p1y.toFixed(1)} ${p2x.toFixed(1)},${p2y.toFixed(1)}`;
}

export function SystemGraph({
  states,
  pulses = [],
  rcKey = null,
  rcShown = false,
  scanning = false,
  recovery = false,
  recoveryId = "",
  interactive = true,
}: {
  states: Record<string, Health>;
  pulses?: Array<[string, string]>;
  rcKey?: string | null;
  rcShown?: boolean;
  scanning?: boolean;
  recovery?: boolean;
  recoveryId?: string;
  interactive?: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(null);

  const lines = useMemo(
    () =>
      EDGES.map((e) => ({
        ...e,
        geom: lineGeom(e.from, e.to),
      })),
    []
  );

  const activePairs = useMemo(
    () => new Set(pulses.map(([a, b]) => `${a}|${b}`)),
    [pulses]
  );

  const isAlert = (from: string, to: string) =>
    states[from] === "down" ||
    states[to] === "down" ||
    activePairs.has(`${from}|${to}`) ||
    activePairs.has(`${to}|${from}`);

  const sel = selected ? nodeMap[selected] : null;

  const isStandby = Object.values(states).every((h) => h === "healthy") && !rcShown && pulses.length === 0;

  return (
    <div className="relative overflow-hidden">
      {/* sweep beam while the AI is investigating */}
      {scanning && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-10 opacity-50"
          style={{
            background:
              "linear-gradient(180deg, transparent 38%, rgba(54,215,232,0.06) 50%, transparent 62%)",
            backgroundSize: "100% 240%",
            animation: "kf-scanbeam 2.6s cubic-bezier(0.45,0,0.55,1) infinite",
          }}
        />
      )}

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block h-auto w-full transition-opacity duration-700"
        style={{ opacity: recovery ? 0.25 : 1 }}
      >
        {/* faint radial backdrop */}
        <circle cx={W / 2} cy={H / 2 - 10} r={205} fill="none" stroke="var(--svg-backdrop)" strokeOpacity={0.8} />
        <circle cx={W / 2} cy={H / 2 - 10} r={300} fill="none" stroke="var(--svg-backdrop)" strokeOpacity={0.6} strokeDasharray="2 10" />

        {/* dependency edges */}
        {lines.map((e) => {
          const alert = isAlert(e.from, e.to);
          const base = alert ? SVG.edgeAlert : e.external ? SVG.edgeExternal : SVG.edgeDefault;
          return (
            <g key={`${e.from}->${e.to}`}>
              <path
                d={e.geom.d}
                fill="none"
                stroke={base}
                strokeOpacity={alert ? 0.7 : e.external ? 0.5 : 0.6}
                strokeWidth={alert ? 2 : 1.4}
                strokeDasharray={e.external ? "3 7" : undefined}
                style={{ transition: "stroke 0.8s, stroke-width 0.8s" }}
              />
              {alert && !e.external && (
                <path
                  d={e.geom.d}
                  fill="none"
                  stroke={SVG.edgeAlert}
                  strokeOpacity={0.6}
                  strokeWidth={1.6}
                  strokeDasharray="1 9"
                  strokeLinecap="round"
                  className="dash-flow"
                />
              )}
              <polygon
                points={arrowPoints(e.geom)}
                fill={alert ? SVG.edgeAlert : e.external ? SVG.edgeExternal : SVG.edgeDefault}
                opacity={alert ? 0.8 : 0.6}
              />
            </g>
          );
        })}

        {/* alert pulses travelling along affected edges */}
        {pulses.map(([from, to], i) => {
          const g = lineGeom(from, to);
          return (
            <g key={`pulse-${from}-${to}`}>
              {[0, 1].map((k) => (
                <circle
                  key={k}
                  r={3}
                  fill="var(--svg-pulse-fill)"
                  style={{ filter: "drop-shadow(0 0 4px var(--shadow-glow-red))" }}
                >
                  <animateMotion dur="1.5s" begin={`${k * 0.75}s`} repeatCount="indefinite" path={g.d} />
                </circle>
              ))}
              <circle
                key={`n${i}`}
                r={3}
                fill="var(--svg-pulse-fill)"
                style={{ filter: "drop-shadow(0 0 4px var(--shadow-glow-red))" }}
              >
                <animateMotion dur="2.6s" begin={`${i * 0.3}s`} repeatCount="indefinite" path={g.d} />
              </circle>
            </g>
          );
        })}

        {/* nodes */}
        {NODES.map((n) => {
          const health = states[n.key] ?? "healthy";
          const color = `var(--svg-node-stroke-${health})`;
          const glow = health === "healthy" ? 6 : health === "degraded" ? 10 : 14;
          const isRc = rcShown && rcKey === n.key;
          const isSel = selected === n.key;
          const statusText = HEALTH_TEXT[health];
          return (
            <g
              key={n.key}
              onClick={() => interactive && setSelected(isSel ? null : n.key)}
              style={{
                cursor: interactive ? "pointer" : "default",
                filter: `drop-shadow(0 0 ${glow}px ${color}${health === "healthy" ? "40" : "70"})`,
                transition: "filter 0.7s ease",
              }}
            >
              {/* down-state pulse rings */}
              {health === "down" && (
                <>
                  <circle
                    className="pulse-ring"
                    cx={n.x}
                    cy={n.y}
                    r={R}
                    fill="none"
                    stroke={color}
                    strokeWidth={1.5}
                  />
                  <circle
                    className="pulse-ring"
                    cx={n.x}
                    cy={n.y}
                    r={R}
                    fill="none"
                    stroke={color}
                    strokeWidth={1}
                    style={{ animationDelay: "0.7s" }}
                  />
                </>
              )}

              {/* degraded rotating halo */}
              {health === "degraded" && (
                <circle
                  className="spin-slow"
                  cx={n.x}
                  cy={n.y}
                  r={R + 5}
                  fill="none"
                  stroke={color}
                  strokeOpacity={0.4}
                  strokeDasharray="3 7"
                  strokeWidth={1.4}
                />
              )}

              {/* healthy breathing halo */}
              {health === "healthy" && (
                <circle
                  className="pulse-ring-soft"
                  cx={n.x}
                  cy={n.y}
                  r={R}
                  fill="none"
                  stroke={color}
                  strokeOpacity={0.3}
                  strokeWidth={1}
                />
              )}

              {/* selected focus ring */}
              {isSel && (
                <circle
                  className="spin-slow"
                  cx={n.x}
                  cy={n.y}
                  r={R + 8}
                  fill="none"
                  stroke="var(--color-accent-cyan)"
                  strokeDasharray="5 5"
                  strokeWidth={1.6}
                />
              )}

              {/* RCA target */}
              {isRc && (
                <g style={{ filter: "drop-shadow(0 0 6px var(--shadow-glow-red))" }}>
                  <circle
                    className="spin-slow"
                    cx={n.x}
                    cy={n.y}
                    r={R + 10}
                    fill="none"
                    stroke={color}
                    strokeDasharray="8 5"
                    strokeWidth={1.8}
                  />
                  <line
                    x1={n.x}
                    y1={n.y - R - 12}
                    x2={n.x}
                    y2={n.y - R - 4}
                    stroke="var(--svg-pulse-fill)"
                    strokeWidth={1}
                    strokeDasharray="2 2"
                  />
                  <text
                    x={n.x}
                    y={n.y - R - 18}
                    textAnchor="middle"
                    fontSize={11}
                    fontFamily="ui-monospace, monospace"
                    letterSpacing="2"
                    fill="var(--svg-pulse-fill)"
                    style={{ filter: "drop-shadow(0 0 5px var(--shadow-glow-red))" }}
                  >
                    ROOT CAUSE
                  </text>
                </g>
              )}

              {/* body */}
              <circle
                cx={n.x}
                cy={n.y}
                r={R}
                fill="var(--svg-node-fill)"
                stroke={color}
                strokeWidth={health === "down" ? 2.2 : 1.6}
                style={{ transition: "stroke 0.7s ease, stroke-width 0.4s" }}
              />
              {n.role === "data" ? (
                <rect
                  x={n.x - 5}
                  y={n.y - 5}
                  width={10}
                  height={10}
                  rx={1}
                  fill={color}
                  opacity={0.9}
                  style={{ transition: "fill 0.7s ease" }}
                />
              ) : n.role === "external" ? (
                <circle cx={n.x} cy={n.y} r={4} fill="none" stroke={color} strokeWidth={1.6} opacity={0.95} />
              ) : (
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={4.4}
                  fill={color}
                  opacity={0.95}
                  style={{ transition: "fill 0.7s ease" }}
                />
              )}

              {/* labels */}
              <text
                x={n.x}
                y={n.y + R + 20}
                textAnchor="middle"
                fontSize={13}
                fontWeight={500}
                fill={health === "down" ? "var(--svg-text-label-down)" : "var(--svg-text-label)"}
                style={{ transition: "fill 0.7s ease" }}
                fontFamily="ui-sans-serif, system-ui, sans-serif"
              >
                {n.label}
              </text>
              <text
                x={n.x}
                y={n.y + R + 36}
                textAnchor="middle"
                fontSize={9}
                fontFamily="ui-monospace, monospace"
                letterSpacing="1.4"
                fill={health === "down" ? color : health === "degraded" ? color : "var(--svg-text-role)"}
                style={{ transition: "fill 0.7s ease" }}
              >
                {statusText || ROLE_TAG[n.role]}
              </text>
            </g>
          );
        })}
      </svg>

      {/* recovery overlay */}
      {recovery && (
        <div
          key={recoveryId || "recovery"}
          className="pointer-events-none absolute inset-0 z-20 grid place-items-center"
        >
          <div
            aria-hidden
            className="recovery-sweep absolute inset-y-0 w-1/3"
            style={{
              background:
                "linear-gradient(100deg, transparent 0%, rgba(0,214,163,0.1) 50%, transparent 100%)",
            }}
          />
          <div
            aria-hidden
            className="absolute rounded-full"
            style={{
              width: 80,
              height: 80,
              border: "1px solid rgba(0,214,163,0.6)",
              animation: "kf-expand 1.5s ease-out both",
              boxShadow: "0 0 40px rgba(0,214,163,0.25)",
            }}
          />
          <div className="anim-pop flex flex-col items-center gap-2 text-center">
            <span className="grid h-11 w-11 place-items-center rounded-full border border-[rgba(0,214,163,0.3)] bg-[rgba(0,214,163,0.08)] text-[var(--color-status-healthy)]">
              <IconCheck className="h-5 w-5" />
            </span>
            <span className="label-micro text-[var(--color-status-healthy)]">
              Incident resolved — all systems nominal
            </span>
          </div>
        </div>
      )}

      {/* standby overlay */}
      {isStandby && !recovery && (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center">
          <div className="text-center">
            <p className="label-micro text-[var(--color-text-faint)] opacity-50">
              all systems operational
            </p>
            <p className="mt-2 font-mono text-[11px] tracking-[0.12em] text-[var(--color-text-faint)] opacity-40">
              {interactive
                ? "click any node to inspect dependencies"
                : "topology snapshot · dependency direction: caller → callee"}
            </p>
          </div>
        </div>
      )}

      {/* node inspector strip */}
      <div className="mt-2 flex min-h-[42px] items-start gap-3 border-t border-[var(--color-border-subtle)] px-1 pt-2.5 text-[11px]">
        {sel ? (
          <>
            <span className="label-micro mt-0.5 text-[var(--color-text-secondary)]">{sel.label}</span>
            <Chip tone={sel && states[sel.key] === "down" ? "crit" : states[sel.key] === "degraded" ? "warn" : "ok"}>
              {states[sel.key]}
            </Chip>
            <Chip tone="neutral">{ROLE_TAG[sel.role]}</Chip>
            <p className="min-w-0 flex-1 leading-snug text-[var(--color-text-muted)]">{sel.blurb}</p>
            <div className="shrink-0 text-right font-mono text-[10px] leading-relaxed text-[var(--color-text-faint)]">
              {sel.calls.length > 0 && (
                <div>
                  calls ▸ <span className="text-[var(--color-accent-cyan)] opacity-80">{sel.calls.join(" · ")}</span>
                </div>
              )}
              <div>
                called by ▸{" "}
                <span className="text-[var(--color-accent-purple)] opacity-80">
                  {dependentsOf(sel.key).length ? dependentsOf(sel.key).join(" · ") : "—"}
                </span>
              </div>
            </div>
          </>
        ) : (
          <span className="font-mono text-[10px] tracking-[0.08em] text-[var(--color-text-faint)]">
            {interactive
              ? "interactive — click any node to inspect its dependencies"
              : "topology snapshot — dependency direction: caller → callee"}
          </span>
        )}
      </div>
    </div>
  );
}

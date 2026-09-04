import type { ReactNode } from "react";

export type Tone = "ok" | "warn" | "crit" | "info" | "system" | "neutral";

export const toneText: Record<Tone, string> = {
  ok: "text-[var(--color-status-healthy)]",
  warn: "text-[var(--color-accent-amber)]",
  crit: "text-[var(--color-accent-red)]",
  info: "text-[var(--color-accent-cyan)]",
  system: "text-[var(--color-accent-purple)]",
  neutral: "text-[var(--color-text-secondary)]",
};

export const toneChip: Record<Tone, string> = {
  ok: "border-[rgba(0,214,163,0.25)] bg-[rgba(0,214,163,0.06)] text-[var(--color-status-healthy)]",
  warn: "border-[rgba(245,184,75,0.25)] bg-[rgba(245,184,75,0.06)] text-[var(--color-accent-amber)]",
  crit: "border-[rgba(255,77,103,0.3)] bg-[rgba(255,77,103,0.08)] text-[var(--color-accent-red)]",
  info: "border-[rgba(54,215,232,0.25)] bg-[rgba(54,215,232,0.06)] text-[var(--color-accent-cyan)]",
  system: "border-[rgba(139,124,255,0.25)] bg-[rgba(139,124,255,0.06)] text-[var(--color-accent-purple)]",
  neutral: "border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.03)] text-[var(--color-text-secondary)]",
};

export const dotColor: Record<Tone, string> = {
  ok: "bg-[var(--color-status-healthy)]",
  warn: "bg-[var(--color-accent-amber)]",
  crit: "bg-[var(--color-accent-red)]",
  info: "bg-[var(--color-accent-cyan)]",
  system: "bg-[var(--color-accent-purple)]",
  neutral: "bg-[var(--color-text-muted)]",
};

/* ── StatusDot ── */

export type StatusDotState = "healthy" | "warning" | "critical" | "neutral";

const dotStateColor: Record<StatusDotState, string> = {
  healthy: "bg-[var(--color-status-healthy)]",
  warning: "bg-[var(--color-accent-amber)]",
  critical: "bg-[var(--color-accent-red)]",
  neutral: "bg-[var(--color-text-muted)]",
};

const dotStateGlow: Record<StatusDotState, string> = {
  healthy: "shadow-[0_0_6px_rgba(0,214,163,0.4)]",
  warning: "shadow-[0_0_6px_rgba(245,184,75,0.4)]",
  critical: "shadow-[0_0_6px_rgba(255,77,103,0.5)]",
  neutral: "",
};

export function StatusDot({
  state = "neutral",
  size = "md",
  pulse = false,
  className = "",
}: {
  state?: StatusDotState;
  size?: "sm" | "md" | "lg";
  pulse?: boolean;
  className?: string;
}) {
  const sz =
    size === "sm" ? "h-1.5 w-1.5" : size === "lg" ? "h-2.5 w-2.5" : "h-2 w-2";
  return (
    <span className={`relative inline-flex ${className}`}>
      {pulse && (
        <span
          className={`absolute inline-flex h-full w-full rounded-full opacity-30 ${dotStateColor[state]}`}
          style={{ animation: "kf-status-pulse 2.4s ease-in-out infinite" }}
        />
      )}
      <span
        className={`relative inline-flex rounded-full ${sz} ${dotStateColor[state]} ${dotStateGlow[state]}`}
      />
    </span>
  );
}

/* ── Button ── */

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

const buttonVariants: Record<ButtonVariant, string> = {
  primary:
    "border border-[rgba(54,215,232,0.3)] bg-[rgba(54,215,232,0.08)] text-[var(--color-accent-cyan)] hover:border-[rgba(54,215,232,0.5)] hover:bg-[rgba(54,215,232,0.15)]",
  secondary:
    "border border-[var(--color-border-default)] text-[var(--color-text-secondary)] hover:border-[var(--color-border-emphasis)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]",
  danger:
    "border border-[rgba(255,77,103,0.35)] bg-[rgba(255,77,103,0.08)] text-[var(--color-accent-red)] hover:border-[rgba(255,77,103,0.55)] hover:bg-[rgba(255,77,103,0.15)]",
  ghost:
    "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]",
};

export function Button({
  variant = "secondary",
  size = "md",
  disabled = false,
  className = "",
  children,
  onClick,
  title,
}: {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  disabled?: boolean;
  className?: string;
  children: ReactNode;
  onClick?: () => void;
  title?: string;
}) {
  const sz =
    size === "sm"
      ? "px-2.5 py-1 text-[12px]"
      : size === "lg"
        ? "px-5 py-2.5 text-[14px]"
        : "px-3.5 py-2 text-[13px]";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-all duration-200 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[rgba(54,215,232,0.4)] disabled:cursor-not-allowed disabled:opacity-40 ${sz} ${buttonVariants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/* ── Metric ── */

export function Metric({
  label,
  value,
  status,
  trend,
  className = "",
}: {
  label: string;
  value: string;
  status?: "ok" | "warn" | "crit";
  trend?: string;
  className?: string;
}) {
  const statusColor =
    status === "ok"
      ? "text-[var(--color-status-healthy)]"
      : status === "warn"
        ? "text-[var(--color-accent-amber)]"
        : status === "crit"
          ? "text-[var(--color-accent-red)]"
          : "text-[var(--color-text-primary)]";

  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <span className="label-micro">{label}</span>
      <div className="flex items-baseline gap-2">
        <span
          className={`font-mono text-[22px] font-semibold tabular-nums tracking-tight ${statusColor}`}
        >
          {value}
        </span>
        {trend && (
          <span className="text-[11px] text-[var(--color-text-muted)]">{trend}</span>
        )}
      </div>
    </div>
  );
}

/* ── Panel ── */

export function Panel({
  title,
  right,
  children,
  className = "",
  bodyClassName = "",
  headerClassName = "",
  flush = false,
}: {
  title?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  headerClassName?: string;
  flush?: boolean;
}) {
  return (
    <section className={`hud-panel rounded-md ${className}`}>
      {title && (
        <header
          className={`flex items-center justify-between gap-3 border-b border-[var(--color-border-subtle)] px-4 py-2.5 ${headerClassName}`}
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent-cyan)] opacity-60" />
            <h2 className="label-micro truncate">{title}</h2>
          </div>
          {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={`${flush ? "" : "p-4"} ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/* ── Chip ── */

export function Chip({
  tone = "neutral",
  children,
  className = "",
  pulse = false,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  pulse?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.1em] ${toneChip[tone]} ${className}`}
    >
      {pulse && (
        <span className="relative flex h-1.5 w-1.5">
          <span
            className={`absolute inline-flex h-full w-full rounded-full opacity-40 ${dotColor[tone]}`}
            style={{ animation: "kf-status-pulse 2s ease-in-out infinite" }}
          />
          <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${dotColor[tone]}`} />
        </span>
      )}
      {children}
    </span>
  );
}

/* ── MicroLabel ── */

export function MicroLabel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={`label-micro ${className}`}>{children}</span>
  );
}

/* ── MonoTick ── */

export function MonoTick({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={`font-mono text-[12px] tracking-wide ${className}`}>
      {children}
    </span>
  );
}

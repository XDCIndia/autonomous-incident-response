"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getAuthSession, logout, type SessionUser } from "@/lib/api";
import {
  IconBolt,
  IconGear,
  IconGrid,
  IconLogout,
  IconPulse,
  IconRadar,
} from "@/components/icons";
import { StatusDot } from "@/components/ui";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Overview", icon: IconGrid, match: (p: string) => p === "/dashboard" || p === "/dashboard/" },
  { href: "/incidents", label: "Incidents", icon: IconBolt, match: (p: string) => p.startsWith("/incidents") },
  { href: "/activity", label: "Activity", icon: IconPulse, match: (p: string) => p.startsWith("/activity") },
  { href: "/monitoring", label: "Monitoring", icon: IconRadar, match: (p: string) => p.startsWith("/monitoring") },
  { href: "/settings", label: "Settings", icon: IconGear, match: (p: string) => p.startsWith("/settings") },
];

function operatorInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "OP";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Persistent console sidebar — brand, primary navigation, and (at the
 * bottom) the authenticated user + logout. Identity comes from the backend
 * session (GET /auth/session), never from client-side storage. Rendered by
 * ConsoleShell: fixed on desktop, inside a drawer on mobile. `onNavigate`
 * closes the mobile drawer after a link tap. */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const [authRequired, setAuthRequired] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAuthSession()
      .then((session) => {
        if (cancelled) return;
        setAuthRequired(session.auth_required);
        setUser(session.authenticated ? session.user : null);
      })
      .catch(() => {
        // Backend unreachable — hide the logout control rather than show one
        // that would fail on click.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      router.replace("/login");
    }
  };

  return (
    <div className="flex h-full flex-col border-r border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)]">
      {/* ── brand ── */}
      <div className="flex items-center gap-3 border-b border-[var(--color-border-subtle)] px-4 py-3.5">
        <div className="relative grid h-8 w-8 shrink-0 place-items-center">
          <div className="absolute inset-0 rounded-full border border-[rgba(54,215,232,0.2)]" />
          <div className="h-2 w-2 rounded-full bg-[var(--color-accent-red)] opacity-80" />
        </div>
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[13px] font-semibold tracking-[0.1em] text-[var(--color-text-primary)]">
            SYSTEM BACHAO
          </div>
          <div className="label-micro truncate text-[var(--color-text-faint)]">
            Incident Response Console
          </div>
        </div>
      </div>

      {/* ── navigation ── */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <div className="label-micro mb-2 px-2 text-[var(--color-text-faint)]">Operations</div>
        <ul className="space-y-1">
          {NAV_ITEMS.map(({ href, label, icon: Icon, match }) => {
            const active = match(pathname);
            return (
              <li key={href}>
                <Link
                  href={href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={`group flex items-center gap-2.5 rounded-md border px-3 py-2 text-[13px] font-medium transition-all duration-150 ${
                    active
                      ? "border-[rgba(54,215,232,0.25)] bg-[rgba(54,215,232,0.07)] text-[var(--color-accent-cyan)]"
                      : "border-transparent text-[var(--color-text-secondary)] hover:border-[var(--color-border-subtle)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                  }`}
                >
                  <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? "" : "opacity-60 group-hover:opacity-90"}`} />
                  <span className="truncate">{label}</span>
                  {active && <span className="ml-auto h-1 w-1 rounded-full bg-[var(--color-accent-cyan)]" />}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* ── user + logout ── */}
      <div className="border-t border-[var(--color-border-subtle)] px-3 py-3">
        <div className="flex items-center gap-2.5 rounded-md px-2 py-1.5">
          <div className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-[rgba(54,215,232,0.25)] bg-[rgba(54,215,232,0.06)] font-mono text-[10px] tracking-wide text-[var(--color-accent-cyan)]">
            {operatorInitials(user?.name ?? "")}
          </div>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[12px] font-medium text-[var(--color-text-primary)]">
              {user?.name || "Operator"}
            </div>
            <div className="flex items-center gap-1.5">
              <StatusDot state="healthy" size="sm" pulse />
              <span
                className="truncate font-mono text-[10px] tracking-[0.08em] text-[var(--color-text-muted)]"
                title={user?.email}
              >
                {user ? user.email.toUpperCase() : "ONLINE"}
              </span>
            </div>
          </div>
        </div>
        {authRequired && (
          <button
            onClick={handleLogout}
            disabled={loggingOut}
            className="mt-1 flex w-full items-center gap-2.5 rounded-md border border-transparent px-4 py-2 text-[12px] font-medium text-[var(--color-text-muted)] transition-all duration-150 hover:border-[rgba(255,77,103,0.25)] hover:bg-[rgba(255,77,103,0.06)] hover:text-[var(--color-accent-red)] disabled:opacity-50"
          >
            <IconLogout className="h-3.5 w-3.5" />
            {loggingOut ? "Signing out…" : "Log Out"}
          </button>
        )}
      </div>
    </div>
  );
}

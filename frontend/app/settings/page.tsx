"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  API_URL,
  getAuthSession,
  logout,
  type SessionUser,
} from "@/lib/api";
import { Button, Chip, Panel, StatusDot } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { ConsoleShell } from "@/components/console/ConsoleShell";

export default function SettingsPage() {
  const router = useRouter();
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [authenticated, setAuthenticated] = useState(false);
  const [backendHealth, setBackendHealth] = useState<"checking" | "ok" | "error">("checking");
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAuthSession()
      .then((s) => {
        if (cancelled) return;
        setAuthRequired(s.auth_required);
        setAuthenticated(s.authenticated);
        setUser(s.authenticated ? s.user : null);
      })
      .catch(() => {
        if (!cancelled) setAuthRequired(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const checkBackend = useCallback(async () => {
    setBackendHealth("checking");
    try {
      const res = await fetch(`${API_URL}/health`);
      setBackendHealth(res.ok ? "ok" : "error");
    } catch {
      setBackendHealth("error");
    }
  }, []);

  useEffect(() => {
    checkBackend();
  }, [checkBackend]);

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      router.replace("/login");
    }
  };

  return (
    <ConsoleShell title="Settings">
      <div className="space-y-6">
        <Panel title="Session">
          <dl className="grid gap-3 sm:grid-cols-2">
            <div className="flex items-center justify-between rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-base)] px-3 py-2.5">
              <dt className="label-micro text-[var(--color-text-muted)]">AUTH MODE</dt>
              <dd>
                {authRequired === null ? (
                  <span className="font-mono text-[11px] text-[var(--color-text-faint)]">checking…</span>
                ) : authRequired ? (
                  <Chip tone={authenticated ? "ok" : "warn"}>{authenticated ? "signed in" : "required"}</Chip>
                ) : (
                  <Chip tone="neutral">open (demo)</Chip>
                )}
              </dd>
            </div>
            <div className="flex items-center justify-between rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-base)] px-3 py-2.5">
              <dt className="label-micro text-[var(--color-text-muted)]">BACKEND API</dt>
              <dd className="flex items-center gap-1.5">
                <StatusDot
                  state={backendHealth === "ok" ? "healthy" : backendHealth === "error" ? "critical" : "neutral"}
                  size="sm"
                  pulse={backendHealth === "ok"}
                />
                <span className="font-mono text-[11px] text-[var(--color-text-secondary)]">
                  {backendHealth === "ok" ? "reachable" : backendHealth === "error" ? "unreachable" : "checking…"}
                </span>
              </dd>
            </div>
          </dl>
          <p className="mt-3 font-mono text-[10px] text-[var(--color-text-faint)]">{API_URL}</p>
        </Panel>

        <Panel title="Account">
          <p className="mb-3 text-[12px] text-[var(--color-text-muted)]">
            Signed-in operator account, verified by the backend on every request.
          </p>
          <dl className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-base)] px-3 py-2.5">
              <dt className="label-micro text-[var(--color-text-muted)]">NAME</dt>
              <dd className="mt-0.5 truncate text-[13px] text-[var(--color-text-primary)]">
                {user?.name ?? "—"}
              </dd>
            </div>
            <div className="rounded-md border border-[var(--color-border-subtle)] bg-[var(--color-bg-base)] px-3 py-2.5">
              <dt className="label-micro text-[var(--color-text-muted)]">EMAIL</dt>
              <dd className="mt-0.5 truncate font-mono text-[12px] text-[var(--color-text-primary)]">
                {user?.email ?? "—"}
              </dd>
            </div>
          </dl>
        </Panel>

        <Panel title="Appearance">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[13px] text-[var(--color-text-primary)]">Theme</div>
              <div className="text-[11px] text-[var(--color-text-muted)]">Dark operations console or light theme.</div>
            </div>
            <ThemeToggle />
          </div>
        </Panel>

        {authRequired && (
          <Panel title="Sign Out">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-[12px] text-[var(--color-text-muted)]">
                End the current dashboard session and return to the sign-in page.
              </p>
              <Button variant="danger" size="md" disabled={loggingOut} onClick={handleLogout}>
                {loggingOut ? "Signing out…" : "Log Out"}
              </Button>
            </div>
          </Panel>
        )}

        <p className="text-center font-mono text-[10px] text-[var(--color-text-faint)]">
          SYSTEM BACHAO · Autonomous Incident Response · hackathon demo build
        </p>
      </div>
    </ConsoleShell>
  );
}

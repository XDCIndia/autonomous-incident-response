"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, Panel } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { CineNetworkBackground } from "@/components/CineNetworkBackground";
import { ApiError, getAuthSession, login } from "@/lib/api";

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="grid min-h-screen place-items-center"><div className="h-8 w-8 rounded-full border-2 border-[var(--color-border-default)] border-t-[var(--color-accent-cyan)] animate-spin" /></div>}>
      <LoginPageInner />
    </Suspense>
  );
}

function LoginPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || "/dashboard";

  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [checkingSession, setCheckingSession] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await getAuthSession();
        if (cancelled) return;
        // Nothing to log into (AUTH_PASSWORD unset) or already logged in —
        // either way, there's no reason to show the form.
        if (!session.auth_required || session.authenticated) {
          router.replace(next);
          return;
        }
      } catch {
        // Backend unreachable — fall through to showing the form; a failed
        // login attempt will surface a clearer error than a silent redirect.
      }
      if (!cancelled) setCheckingSession(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [next, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) {
      setError("Enter the password.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await login(password);
      router.replace(next);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? "Incorrect password." : "Unable to log in — try again.");
    } finally {
      setSubmitting(false);
    }
  };

  if (checkingSession) {
    return (
      <div className="grid min-h-screen place-items-center">
        <div className="text-center space-y-4">
          <div className="mx-auto h-8 w-8 rounded-full border-2 border-[var(--color-border-default)] border-t-[var(--color-accent-cyan)] animate-spin" />
          <p className="font-mono text-[11px] tracking-[0.14em] text-[var(--color-text-muted)]">checking session</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <CineNetworkBackground />
      <header className="glass-nav sticky top-0 z-40">
        <div className="mx-auto flex max-w-[1280px] items-center gap-4 px-4 py-3">
          <Link href="/" className="flex min-w-0 items-center gap-3">
            <div className="relative grid h-8 w-8 shrink-0 place-items-center">
              <div className="absolute inset-0 rounded-full border border-[rgba(54,215,232,0.2)]" />
              <div className="h-2 w-2 rounded-full bg-[var(--color-accent-red)] opacity-80" />
            </div>
            <div className="leading-tight">
              <div className="text-[14px] font-semibold tracking-[0.1em] text-[var(--color-text-primary)]">
                SYSTEM BACHAO
              </div>
              <div className="label-micro text-[var(--color-text-faint)]">Operations Dashboard</div>
            </div>
          </Link>
          <div className="ml-auto">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto flex max-w-[420px] items-center px-4 py-24">
        <Panel title="Log In" className="w-full">
          <p className="-mt-1 mb-4 text-[12px] text-[var(--color-text-muted)]">
            Enter the shared dashboard password to continue.
          </p>
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <div>
              <label className="label-micro mb-1 block text-[var(--color-text-muted)]">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
                className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-base)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent-cyan)]"
              />
            </div>
            {error && <p className="text-[13px] text-[var(--color-accent-red)]">{error}</p>}
            <Button variant="primary" size="md" disabled={submitting}>
              {submitting ? "Logging in…" : "Log In"}
            </Button>
          </form>
        </Panel>
      </main>
    </div>
  );
}

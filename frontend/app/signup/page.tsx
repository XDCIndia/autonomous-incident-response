"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, Panel } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { CineNetworkBackground } from "@/components/CineNetworkBackground";
import { ApiError, getAuthSession, signup } from "@/lib/api";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function SignupPage() {
  return (
    <Suspense fallback={<div className="grid min-h-screen place-items-center"><div className="h-8 w-8 rounded-full border-2 border-[var(--color-border-default)] border-t-[var(--color-accent-cyan)] animate-spin" /></div>}>
      <SignupPageInner />
    </Suspense>
  );
}

function SignupPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || "/dashboard";

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [checkingSession, setCheckingSession] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await getAuthSession();
        if (cancelled) return;
        // Already logged in — go straight to the console.
        if (session.authenticated) {
          router.replace(next);
          return;
        }
      } catch {
        // Backend unreachable — show the form; submit will surface an error.
      }
      if (!cancelled) {
        setCheckingSession(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [next, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Enter your full name.");
      return;
    }
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setError("Enter your email address.");
      return;
    }
    if (!EMAIL_RE.test(trimmedEmail)) {
      setError("Enter a valid email address.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await signup(name.trim(), trimmedEmail, password);
      router.replace(next);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError("An account with this email already exists.");
      } else if (e instanceof ApiError && e.status === 422) {
        setError(e.message || "Please check the fields and try again.");
      } else {
        setError("Unable to create your account — try again.");
      }
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
        <Panel title="Create Account" className="w-full">
          <p className="-mt-1 mb-4 text-[12px] text-[var(--color-text-muted)]">
            Create an operator account for the incident response console.
          </p>
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <div>
              <label htmlFor="name" className="label-micro mb-1 block text-[var(--color-text-muted)]">
                Full name
              </label>
              <input
                id="name"
                type="text"
                autoComplete="name"
                placeholder="Your full name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-base)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none transition-colors placeholder:text-[var(--color-text-faint)] focus:border-[var(--color-accent-cyan)]"
              />
            </div>
            <div>
              <label htmlFor="email" className="label-micro mb-1 block text-[var(--color-text-muted)]">
                Email address
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-base)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none transition-colors placeholder:text-[var(--color-text-faint)] focus:border-[var(--color-accent-cyan)]"
              />
            </div>
            <div>
              <label htmlFor="password" className="label-micro mb-1 block text-[var(--color-text-muted)]">
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-base)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none transition-colors placeholder:text-[var(--color-text-faint)] focus:border-[var(--color-accent-cyan)]"
              />
            </div>
            <div>
              <label htmlFor="confirm" className="label-micro mb-1 block text-[var(--color-text-muted)]">
                Confirm password
              </label>
              <input
                id="confirm"
                type="password"
                autoComplete="new-password"
                placeholder="Confirm your password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="w-full rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-base)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] outline-none transition-colors placeholder:text-[var(--color-text-faint)] focus:border-[var(--color-accent-cyan)]"
              />
            </div>
            {error && (
              <p role="alert" className="rounded border border-[rgba(255,77,103,0.3)] bg-[rgba(255,77,103,0.07)] px-3 py-2 text-[12px] text-[var(--color-accent-red)]">
                {error}
              </p>
            )}
            <Button variant="primary" size="md" disabled={submitting}>
              {submitting ? "Creating account…" : "Create Account"}
            </Button>
          </form>

          <p className="mt-4 text-center text-[12px] text-[var(--color-text-muted)]">
            Already have an account?{" "}
            <Link href="/login" className="text-[var(--color-accent-cyan)] hover:underline">
              Sign in
            </Link>
          </p>
        </Panel>
      </main>
    </div>
  );
}

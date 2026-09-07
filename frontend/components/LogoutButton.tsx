"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getAuthSession, logout } from "@/lib/api";

/** Renders nothing when login isn't configured for this deployment
 * (AUTH_PASSWORD unset) — there's no session to log out of, and showing a
 * logout control that does nothing meaningful would be confusing. */
export function LogoutButton() {
  const router = useRouter();
  const [authRequired, setAuthRequired] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAuthSession()
      .then((session) => {
        if (!cancelled) setAuthRequired(session.auth_required);
      })
      .catch(() => {
        // Unable to reach the backend — stay hidden rather than show a
        // control that would just fail if clicked.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!authRequired) return null;

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      router.replace("/login");
    }
  };

  return (
    <button
      onClick={handleLogout}
      disabled={loggingOut}
      className="label-micro text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text-primary)] disabled:opacity-50"
    >
      {loggingOut ? "Logging out…" : "Log Out"}
    </button>
  );
}

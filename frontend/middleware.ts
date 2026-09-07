import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Middleware is a UX convenience only — it avoids the protected page's
// shell flashing before a client-side fetch would 401. It is NOT the real
// authorization boundary: the backend rejecting any data fetch with 401
// when the session is missing/invalid (see lib/api.ts's request()) is what
// actually protects the data, and that check runs regardless of this file.
//
// Why this needs a backend call at all: the backend's real session cookie
// is set on, and scoped to, the BACKEND's own origin (POST /auth/login is
// called directly from the browser to that origin) — a request to THIS
// app's origin (what middleware sees) never carries it, cross-origin cookie
// scoping being what it is. So middleware cannot check "is this specific
// browser logged in" at all. What it CAN check, safely and without any
// per-user secret, is the one thing that's actually global: whether login
// is even required for this deployment (Settings.auth_password configured
// or not) — combined with a same-origin marker cookie this app's own
// login page sets client-side right after a real, successful login
// (lib/api.ts's UI_SESSION_MARKER). Neither piece alone is meaningful;
// together they're enough to decide whether to redirect, while every
// actual data fetch still goes through the real backend check regardless.
//
// Fails OPEN (allows the request through) if the backend can't be reached
// at all — a transient backend blip locking everyone out of even the page
// shell would be worse than falling back to the real per-fetch 401 check.
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const MARKER_COOKIE = "sb_ui_session";

export async function middleware(request: NextRequest) {
  let authRequired = false;
  try {
    const res = await fetch(`${API_URL}/auth/session`, {
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return NextResponse.next();
    const session = (await res.json()) as { authenticated: boolean; auth_required: boolean };
    authRequired = session.auth_required;
  } catch {
    return NextResponse.next();
  }

  if (!authRequired) return NextResponse.next();
  if (request.cookies.has(MARKER_COOKIE)) return NextResponse.next();

  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/dashboard/:path*", "/incidents/:path*"],
};

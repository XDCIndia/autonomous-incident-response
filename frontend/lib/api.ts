// Centralized API client — every backend call in the app goes through here.
// Do not scatter fetch() calls in components; do not duplicate these types.

import type {
  ApprovalResponse,
  ApprovalStatus,
  Incident,
  IncidentSummary,
  KnowledgeBaseResult,
  MonitoredTarget,
  ServiceHealth,
  TriggerResponse,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

// Optional — only needed when the backend has API_KEY set (backend/api/app.py
// require_api_key). Unset by default, matching the backend's own default of
// auth disabled for local dev. See .env.example's API_KEY/CORS_ORIGINS.
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// A same-origin (this app's own :3000/whatever origin), non-httpOnly marker
// cookie — NOT the actual session credential. The real session lives in an
// httpOnly cookie scoped to the backend's own origin (set by POST
// /auth/login, sent automatically on every `credentials: "include"` fetch
// below); this marker only exists so frontend/middleware.ts — which runs
// server-side for THIS app's origin and therefore never sees the backend's
// cross-origin cookie at all — has something same-origin to check before
// rendering a protected page. It carries no secret and proves nothing by
// itself: every real data fetch below still requires the backend to accept
// the actual session cookie, which is the real authorization boundary.
const UI_SESSION_MARKER = "sb_ui_session";

function setUiSessionMarker(present: boolean) {
  if (typeof document === "undefined") return;
  document.cookie = present
    ? `${UI_SESSION_MARKER}=1; path=/; samesite=lax`
    : `${UI_SESSION_MARKER}=; path=/; samesite=lax; max-age=0`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) };
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
  } catch {
    throw new ApiError("Failed to connect to backend", 0);
  }
  if (res.status === 401 && path !== "/auth/login") {
    // Session expired, was never established, or got invalidated elsewhere
    // (e.g. logged out in another tab). Clear the UI-side marker and send
    // the user back to log in again — this is the client reacting to the
    // backend's real authorization decision, not making one of its own.
    setUiSessionMarker(false);
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.detail || `Request failed (${res.status})`, res.status);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Dashboard auth — multi-user accounts.
// ---------------------------------------------------------------------------
// Identity comes from the backend (GET /auth/session returns the
// authenticated user's name/email); there is deliberately no client-side
// identity store. The session itself is the backend's httpOnly cookie,
// sent automatically on every `credentials: "include"` fetch.

export interface SessionUser {
  name: string;
  email: string;
}

export interface AuthSession {
  authenticated: boolean;
  auth_required: boolean;
  user: SessionUser | null;
}

export function getAuthSession(): Promise<AuthSession> {
  return request<AuthSession>("/auth/session");
}

export async function signup(name: string, email: string, password: string): Promise<AuthSession> {
  const session = await request<AuthSession>("/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, email, password }),
  });
  setUiSessionMarker(true);
  return session;
}

export async function login(email: string, password: string): Promise<AuthSession> {
  const session = await request<AuthSession>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  setUiSessionMarker(true);
  return session;
}

export async function logout(): Promise<void> {
  try {
    await request<AuthSession>("/auth/logout", { method: "POST" });
  } finally {
    setUiSessionMarker(false);
  }
}

export function listIncidents(limit = 50): Promise<IncidentSummary[]> {
  return request<IncidentSummary[]>(`/incidents?limit=${limit}`);
}

export function getIncident(id: string): Promise<Incident> {
  return request<Incident>(`/incidents/${id}`);
}

export function triggerIncident(serviceName: string, scenario: string): Promise<TriggerResponse> {
  return request<TriggerResponse>(`/incidents/trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service_name: serviceName, scenario }),
  });
}

export function getApprovalStatus(id: string): Promise<ApprovalStatus> {
  return request<ApprovalStatus>(`/incidents/${id}/approval`);
}

export function approveIncident(id: string): Promise<ApprovalResponse> {
  return request<ApprovalResponse>(`/incidents/${id}/approve`, { method: "POST" });
}

export function rejectIncident(id: string): Promise<ApprovalResponse> {
  return request<ApprovalResponse>(`/incidents/${id}/reject`, { method: "POST" });
}

export function searchKnowledgeBase(
  query: string,
  topK = 5
): Promise<{ query: string; results: KnowledgeBaseResult[] }> {
  return request(`/knowledge-base/search?query=${encodeURIComponent(query)}&top_k=${topK}`);
}

// Returns null (rather than throwing) when the health endpoint 503s because
// no DockerController is available in this environment — that's an expected,
// displayable "unknown" state, not an application error.
export async function getServiceHealth(service: string): Promise<ServiceHealth | null> {
  try {
    return await request<ServiceHealth>(`/services/health?service=${encodeURIComponent(service)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 503) return null;
    throw e;
  }
}

export function incidentWebSocketUrl(id: string): string {
  return `${WS_URL}/ws/incidents/${id}`;
}

// ---------------------------------------------------------------------------
// Monitored targets (real URL monitoring — backend.monitoring.url_monitor)
// ---------------------------------------------------------------------------

export function listTargets(): Promise<MonitoredTarget[]> {
  return request<MonitoredTarget[]>(`/targets`);
}

export function getTarget(id: string): Promise<MonitoredTarget> {
  return request<MonitoredTarget>(`/targets/${id}`);
}

export function createTarget(name: string, url: string): Promise<MonitoredTarget> {
  return request<MonitoredTarget>(`/targets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, url }),
  });
}

export function deleteTarget(id: string): Promise<{ status: string; id: string }> {
  return request(`/targets/${id}`, { method: "DELETE" });
}

export function setTargetMonitoring(id: string, enabled: boolean): Promise<MonitoredTarget> {
  return request<MonitoredTarget>(`/targets/${id}/monitoring`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

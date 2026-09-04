// Centralized API client — every backend call in the app goes through here.
// Do not scatter fetch() calls in components; do not duplicate these types.

import type {
  ApprovalResponse,
  ApprovalStatus,
  Incident,
  IncidentSummary,
  KnowledgeBaseResult,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) };
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError("Failed to connect to backend", 0);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.detail || `Request failed (${res.status})`, res.status);
  }
  return res.json() as Promise<T>;
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

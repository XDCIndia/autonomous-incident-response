"use client";

import { useState, useEffect, useRef } from "react";
import { useParams } from "next/navigation";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

interface TimelineEvent {
  id: string;
  timestamp: string;
  stage: string;
  status: string;
  message: string;
  metadata: Record<string, unknown>;
}

interface Incident {
  id: string;
  service_name: string;
  state: string;
  severity: string | null;
  autonomy_level: string | null;
  current_stage: string | null;
  created_at: string;
  severity_result: {
    blast_radius: number;
    affected_services: string[];
    justification: string;
  } | null;
  remediation_request: {
    action: string;
    description: string;
    target_service: string;
    requires_approval: boolean;
  } | null;
  remediation_result: {
    action: string;
    success: boolean;
    message: string;
  } | null;
  report: {
    root_cause: string;
    impact: string;
    remediation_action: string;
    confidence: number;
    prevention: string;
    result_metrics: Record<string, { before: unknown; after: unknown }>;
  } | null;
}

// Static service topology — mirrors backend/simulator/service_graph.py's
// get_default_graph(). Not exposed via API (Role 3's module), so kept as a
// frontend-side constant purely for blast-radius layout purposes.
const SERVICE_TOPOLOGY: { name: string; dependsOn: string[] }[] = [
  { name: "gateway", dependsOn: [] },
  { name: "order-service", dependsOn: ["gateway"] },
  { name: "payment-service", dependsOn: ["order-service"] },
  { name: "shipping-service", dependsOn: ["order-service"] },
  { name: "inventory-service", dependsOn: ["payment-service"] },
];

export default function IncidentPage() {
  const params = useParams();
  const id = params.id as string;

  const [incident, setIncident] = useState<Incident | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [pendingApproval, setPendingApproval] = useState(false);
  const [approvalSubmitting, setApprovalSubmitting] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const refreshIncident = () => {
    fetch(`${API_URL}/incidents/${id}`)
      .then((res) => res.json())
      .then(setIncident)
      .catch(console.error);
  };

  const refreshApproval = () => {
    fetch(`${API_URL}/incidents/${id}/approval`)
      .then((res) => res.json())
      .then((data) => setPendingApproval(Boolean(data.has_pending_approval)))
      .catch(console.error);
  };

  // Fetch incident details
  useEffect(() => {
    refreshIncident();
    refreshApproval();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // WebSocket for live timeline
  useEffect(() => {
    const ws = new WebSocket(`${WS_URL}/ws/incidents/${id}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      console.log("WebSocket connected");
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "ping") return; // keepalive

      setTimeline((prev) => {
        // Avoid duplicates
        if (prev.some((e) => e.id === data.id)) return prev;
        return [...prev, data];
      });

      // Refresh incident details + approval status on new events
      refreshIncident();
      refreshApproval();
    };

    ws.onclose = () => {
      setConnected(false);
      console.log("WebSocket disconnected");
    };

    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const submitApproval = async (approved: boolean) => {
    setApprovalSubmitting(true);
    setApprovalError(null);
    try {
      const res = await fetch(`${API_URL}/incidents/${id}/${approved ? "approve" : "reject"}`, {
        method: "POST",
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setApprovalError(err.detail || "Failed to submit decision");
        return;
      }
      refreshApproval();
      refreshIncident();
    } catch (e) {
      setApprovalError("Failed to connect to backend");
    } finally {
      setApprovalSubmitting(false);
    }
  };

  if (!incident) {
    return <p>Loading incident...</p>;
  }

  return (
    <div>
      <a href="/" style={{ color: "#0066cc", marginBottom: "16px", display: "inline-block" }}>
        ← Back to Dashboard
      </a>

      {pendingApproval && (
        <div style={{
          padding: "16px",
          marginBottom: "24px",
          background: "#fff3cd",
          border: "1px solid #ffe69c",
          borderRadius: "8px",
        }}>
          <h3 style={{ margin: "0 0 8px" }}>🟡 Approval Required</h3>
          <p style={{ margin: "0 0 12px" }}>
            Remediation <strong>{incident.remediation_request?.action ?? "—"}</strong> on{" "}
            <strong>{incident.remediation_request?.target_service ?? incident.service_name}</strong> needs
            human approval before it executes ({incident.severity} — semi-autonomous tier).
          </p>
          {incident.remediation_request?.description && (
            <p style={{ margin: "0 0 12px", color: "#555", fontSize: "13px" }}>
              {incident.remediation_request.description}
            </p>
          )}
          <div style={{ display: "flex", gap: "12px" }}>
            <button
              onClick={() => submitApproval(true)}
              disabled={approvalSubmitting}
              style={{
                padding: "8px 20px",
                border: "1px solid #2e7d32",
                borderRadius: "6px",
                background: "#2e7d32",
                color: "#fff",
                cursor: approvalSubmitting ? "not-allowed" : "pointer",
              }}
            >
              Approve
            </button>
            <button
              onClick={() => submitApproval(false)}
              disabled={approvalSubmitting}
              style={{
                padding: "8px 20px",
                border: "1px solid #c62828",
                borderRadius: "6px",
                background: "#fff",
                color: "#c62828",
                cursor: approvalSubmitting ? "not-allowed" : "pointer",
              }}
            >
              Reject
            </button>
          </div>
          {approvalError && (
            <p style={{ marginTop: "8px", color: "#c62828", fontSize: "13px" }}>{approvalError}</p>
          )}
        </div>
      )}

      {incident.severity_result && (
        <div style={{ padding: "16px", marginBottom: "24px", border: "1px solid #eee", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 4px" }}>Blast Radius</h3>
          <p style={{ margin: "0 0 12px", color: "#666", fontSize: "13px" }}>
            {incident.severity_result.justification}
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center" }}>
            {SERVICE_TOPOLOGY.map((svc, i) => {
              const isPrimary = svc.name === incident.service_name;
              const isAffected = incident.severity_result!.affected_services.includes(svc.name);
              const bg = isPrimary ? "#f8d7da" : isAffected ? "#ffe5b4" : "#f0f0f0";
              const border = isPrimary ? "#dc3545" : isAffected ? "#fd7e14" : "#ddd";
              return (
                <span key={svc.name} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span
                    title={svc.dependsOn.length ? `depends on: ${svc.dependsOn.join(", ")}` : "entry point"}
                    style={{
                      padding: "6px 12px",
                      borderRadius: "6px",
                      background: bg,
                      border: `1px solid ${border}`,
                      fontSize: "13px",
                      fontWeight: isPrimary || isAffected ? 600 : 400,
                    }}
                  >
                    {isPrimary ? "🔴 " : isAffected ? "🟠 " : ""}
                    {svc.name}
                  </span>
                  {i < SERVICE_TOPOLOGY.length - 1 && <span style={{ color: "#bbb" }}>→</span>}
                </span>
              );
            })}
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px", marginBottom: "32px" }}>
        <div style={{ padding: "16px", border: "1px solid #eee", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 12px" }}>Incident Details</h3>
          <dl style={{ margin: 0 }}>
            <dt style={{ fontWeight: "bold" }}>ID</dt>
            <dd>{incident.id}</dd>
            <dt style={{ fontWeight: "bold" }}>Service</dt>
            <dd>{incident.service_name}</dd>
            <dt style={{ fontWeight: "bold" }}>State</dt>
            <dd>
              <span style={{
                padding: "2px 8px",
                borderRadius: "4px",
                background: incident.state === "resolved" ? "#d4edda" : incident.state === "failed" ? "#f8d7da" : "#fff3cd",
              }}>
                {incident.state}
              </span>
            </dd>
            <dt style={{ fontWeight: "bold" }}>Severity</dt>
            <dd>{incident.severity || "—"}</dd>
            <dt style={{ fontWeight: "bold" }}>Stage</dt>
            <dd>{incident.current_stage || "—"}</dd>
          </dl>
        </div>

        <div style={{ padding: "16px", border: "1px solid #eee", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 12px" }}>
            Live Timeline
            <span style={{ fontSize: "12px", color: connected ? "green" : "red", marginLeft: "8px" }}>
              {connected ? "● Connected" : "○ Disconnected"}
            </span>
          </h3>
          <div style={{ maxHeight: "300px", overflowY: "auto" }}>
            {timeline.length === 0 ? (
              <p style={{ color: "#666" }}>Waiting for events...</p>
            ) : (
              timeline.map((event) => (
                <div key={event.id} style={{
                  padding: "8px",
                  marginBottom: "4px",
                  background: event.status === "failed" ? "#fee" : event.status === "started" ? "#fff3cd" : "#f0f0f0",
                  borderRadius: "4px",
                  fontSize: "13px",
                }}>
                  <strong>{event.stage}</strong> — {event.message}
                  <br />
                  <small style={{ color: "#666" }}>
                    {new Date(event.timestamp).toLocaleTimeString()} · {event.status}
                  </small>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {incident.report && (
        <div style={{ padding: "16px", border: "1px solid #eee", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 12px" }}>Incident Report</h3>
          <dl style={{ margin: 0 }}>
            <dt style={{ fontWeight: "bold" }}>Root Cause</dt>
            <dd>{incident.report.root_cause}</dd>
            <dt style={{ fontWeight: "bold" }}>Impact</dt>
            <dd>{incident.report.impact}</dd>
            <dt style={{ fontWeight: "bold" }}>Remediation</dt>
            <dd>{incident.report.remediation_action}</dd>
            <dt style={{ fontWeight: "bold" }}>Confidence</dt>
            <dd>{(incident.report.confidence * 100).toFixed(0)}%</dd>
            <dt style={{ fontWeight: "bold" }}>Prevention</dt>
            <dd>{incident.report.prevention}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}

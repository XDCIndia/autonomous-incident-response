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

interface LogInvestigationResult {
  hypothesis: string;
  evidence: string[];
  confidence: number;
  suggested_root_cause: string;
}

interface MetricInvestigationResult {
  hypothesis: string;
  evidence: string[];
  confidence: number;
  suggested_root_cause: string;
  metrics_summary: Record<string, unknown>;
}

interface ArbiterResult {
  merged_hypothesis: string;
  root_cause: string;
  confidence: number;
  log_hypothesis_agrees: boolean;
  metric_hypothesis_agrees: boolean;
  conflict_description: string | null;
  evidence: string[];
  contributing_factors: string[];
}

interface Incident {
  id: string;
  service_name: string;
  state: string;
  severity: string | null;
  current_stage: string | null;
  created_at: string;
  log_result: LogInvestigationResult | null;
  metric_result: MetricInvestigationResult | null;
  arbiter_result: ArbiterResult | null;
  report: {
    root_cause: string;
    impact: string;
    remediation_action: string;
    confidence: number;
    prevention: string;
    result_metrics: Record<string, { before: unknown; after: unknown }>;
  } | null;
}

interface SimilarIncident {
  id: number;
  incident_id: string | null;
  service: string;
  root_cause: string;
  description: string;
  resolved_via: string;
  created_at: string;
  similarity: number;
}

export default function IncidentPage() {
  const params = useParams();
  const id = params.id as string;

  const [incident, setIncident] = useState<Incident | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [similarIncidents, setSimilarIncidents] = useState<SimilarIncident[] | null>(null);
  const [similarLoading, setSimilarLoading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // Fetch incident details
  useEffect(() => {
    fetch(`${API_URL}/incidents/${id}`)
      .then((res) => res.json())
      .then(setIncident)
      .catch(console.error);
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

      // Refresh incident details on new events
      fetch(`${API_URL}/incidents/${id}`)
        .then((res) => res.json())
        .then(setIncident)
        .catch(console.error);
    };

    ws.onclose = () => {
      setConnected(false);
      console.log("WebSocket disconnected");
    };

    return () => ws.close();
  }, [id]);

  // Once the report lands (root cause is final), look up similar past
  // incidents from the knowledge base — keyed on service+root_cause rather
  // than the whole `incident` object so it doesn't re-fetch on every
  // WebSocket-triggered refresh once the report itself hasn't changed.
  const reportRootCause = incident?.report?.root_cause;
  useEffect(() => {
    if (!incident || !reportRootCause) return;

    setSimilarLoading(true);
    const query = `${incident.service_name} ${reportRootCause}`;
    fetch(`${API_URL}/knowledge-base/search?query=${encodeURIComponent(query)}&top_k=3`)
      .then((res) => res.json())
      .then((data) => setSimilarIncidents(data.results ?? []))
      .catch(console.error)
      .finally(() => setSimilarLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incident?.service_name, reportRootCause]);

  if (!incident) {
    return <p>Loading incident...</p>;
  }

  return (
    <div>
      <a href="/" style={{ color: "#0066cc", marginBottom: "16px", display: "inline-block" }}>
        ← Back to Dashboard
      </a>

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

      {(incident.log_result || incident.metric_result) && (
        <div style={{ padding: "16px", border: "1px solid #eee", borderRadius: "8px", marginBottom: "32px" }}>
          <h3 style={{ margin: "0 0 12px" }}>Investigation</h3>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px" }}>
            <div>
              <h4 style={{ margin: "0 0 8px", fontSize: "13px", color: "#666", textTransform: "uppercase" }}>
                Log Investigator
              </h4>
              {incident.log_result ? (
                <dl style={{ margin: 0 }}>
                  <dt style={{ fontWeight: "bold" }}>Hypothesis</dt>
                  <dd>{incident.log_result.hypothesis}</dd>
                  <dt style={{ fontWeight: "bold" }}>Suggested Root Cause</dt>
                  <dd>{incident.log_result.suggested_root_cause}</dd>
                  <dt style={{ fontWeight: "bold" }}>Confidence</dt>
                  <dd>{(incident.log_result.confidence * 100).toFixed(0)}%</dd>
                </dl>
              ) : (
                <p style={{ color: "#666" }}>No log evidence.</p>
              )}
            </div>
            <div>
              <h4 style={{ margin: "0 0 8px", fontSize: "13px", color: "#666", textTransform: "uppercase" }}>
                Metric Investigator
              </h4>
              {incident.metric_result ? (
                <dl style={{ margin: 0 }}>
                  <dt style={{ fontWeight: "bold" }}>Hypothesis</dt>
                  <dd>{incident.metric_result.hypothesis}</dd>
                  <dt style={{ fontWeight: "bold" }}>Suggested Root Cause</dt>
                  <dd>{incident.metric_result.suggested_root_cause}</dd>
                  <dt style={{ fontWeight: "bold" }}>Confidence</dt>
                  <dd>{(incident.metric_result.confidence * 100).toFixed(0)}%</dd>
                </dl>
              ) : (
                <p style={{ color: "#666" }}>No metric evidence.</p>
              )}
            </div>
          </div>

          {incident.arbiter_result && (
            <div style={{ marginTop: "16px", paddingTop: "16px", borderTop: "1px solid #eee" }}>
              <h4 style={{ margin: "0 0 8px", fontSize: "13px", color: "#666", textTransform: "uppercase" }}>
                Arbiter
              </h4>
              {incident.arbiter_result.conflict_description && (
                <div
                  style={{
                    padding: "10px 12px",
                    marginBottom: "10px",
                    background: "#fff3cd",
                    border: "1px solid #ffe69c",
                    borderRadius: "6px",
                    fontSize: "13px",
                  }}
                >
                  <strong>⚠ Conflict:</strong> {incident.arbiter_result.conflict_description}
                </div>
              )}
              <dl style={{ margin: 0 }}>
                <dt style={{ fontWeight: "bold" }}>Merged Hypothesis</dt>
                <dd>{incident.arbiter_result.merged_hypothesis}</dd>
                <dt style={{ fontWeight: "bold" }}>Root Cause</dt>
                <dd>{incident.arbiter_result.root_cause}</dd>
                <dt style={{ fontWeight: "bold" }}>Confidence</dt>
                <dd>{(incident.arbiter_result.confidence * 100).toFixed(0)}%</dd>
                {incident.arbiter_result.contributing_factors.length > 0 && (
                  <>
                    <dt style={{ fontWeight: "bold" }}>Contributing Factors</dt>
                    <dd>{incident.arbiter_result.contributing_factors.join(", ")}</dd>
                  </>
                )}
              </dl>
            </div>
          )}
        </div>
      )}

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

      {incident.report && (
        <div style={{ padding: "16px", border: "1px solid #eee", borderRadius: "8px", marginTop: "24px" }}>
          <h3 style={{ margin: "0 0 12px" }}>Similar Past Incidents</h3>
          {similarLoading ? (
            <p style={{ color: "#666" }}>Searching knowledge base...</p>
          ) : !similarIncidents || similarIncidents.length === 0 ? (
            <p style={{ color: "#666" }}>No similar past incidents found.</p>
          ) : (
            similarIncidents.map((match) => (
              <div
                key={match.id}
                style={{
                  padding: "10px 12px",
                  marginBottom: "8px",
                  background: "#f8f9fa",
                  border: "1px solid #eee",
                  borderRadius: "6px",
                  fontSize: "13px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                  <strong>
                    {match.service} — {match.root_cause}
                  </strong>
                  <span style={{ color: "#666" }}>{(match.similarity * 100).toFixed(0)}% match</span>
                </div>
                <p style={{ margin: "0 0 4px" }}>{match.description}</p>
                <small style={{ color: "#666" }}>Resolved via: {match.resolved_via}</small>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

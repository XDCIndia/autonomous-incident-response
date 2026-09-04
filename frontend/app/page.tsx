"use client";

import { useState, useEffect } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface IncidentSummary {
  id: string;
  service_name: string;
  state: string;
  severity: string | null;
  current_stage: string | null;
  created_at: string;
}

const SCENARIOS = [
  { id: "bad_deployment", label: "🔴 Bad Deployment", service: "payment-service" },
  { id: "database_failure", label: "🟠 Database Failure", service: "inventory-service" },
  { id: "dependency_outage", label: "🟡 Dependency Outage", service: "payment-service" },
  { id: "resource_exhaustion", label: "🔵 Resource Exhaustion", service: "order-service" },
];

export default function Dashboard() {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchIncidents = async () => {
    try {
      const res = await fetch(`${API_URL}/incidents`);
      if (res.ok) {
        setIncidents(await res.json());
      }
    } catch (e) {
      console.error("Failed to fetch incidents:", e);
    }
  };

  useEffect(() => {
    fetchIncidents();
    const interval = setInterval(fetchIncidents, 5000);
    return () => clearInterval(interval);
  }, []);

  const triggerIncident = async (scenario: string, service: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/incidents/trigger`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service_name: service, scenario }),
      });
      if (res.ok) {
        const data = await res.json();
        console.log("Incident triggered:", data.incident_id);
        fetchIncidents();
      } else {
        const err = await res.json();
        setError(err.detail || "Failed to trigger incident");
      }
    } catch (e) {
      setError("Failed to connect to backend");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h2>Trigger Incident</h2>
      <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", marginBottom: "32px" }}>
        {SCENARIOS.map((s) => (
          <button
            key={s.id}
            onClick={() => triggerIncident(s.id, s.service)}
            disabled={loading}
            style={{
              padding: "12px 20px",
              border: "1px solid #ddd",
              borderRadius: "8px",
              background: loading ? "#f5f5f5" : "#fff",
              cursor: loading ? "not-allowed" : "pointer",
              fontSize: "14px",
            }}
          >
            {s.label}
            <br />
            <small style={{ color: "#666" }}>{s.service}</small>
          </button>
        ))}
      </div>

      {error && (
        <div style={{ padding: "12px", background: "#fee", border: "1px solid #fcc", borderRadius: "8px", marginBottom: "16px" }}>
          {error}
        </div>
      )}

      <h2>Recent Incidents</h2>
      {incidents.length === 0 ? (
        <p style={{ color: "#666" }}>No incidents yet. Trigger one above!</p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "2px solid #eee", textAlign: "left" }}>
              <th style={{ padding: "8px" }}>ID</th>
              <th style={{ padding: "8px" }}>Service</th>
              <th style={{ padding: "8px" }}>State</th>
              <th style={{ padding: "8px" }}>Severity</th>
              <th style={{ padding: "8px" }}>Stage</th>
              <th style={{ padding: "8px" }}>Created</th>
            </tr>
          </thead>
          <tbody>
            {incidents.map((inc) => (
              <tr key={inc.id} style={{ borderBottom: "1px solid #eee" }}>
                <td style={{ padding: "8px" }}>
                  <a href={`/incidents/${inc.id}`} style={{ color: "#0066cc" }}>
                    {inc.id.slice(0, 8)}...
                  </a>
                </td>
                <td style={{ padding: "8px" }}>{inc.service_name}</td>
                <td style={{ padding: "8px" }}>
                  <span style={{
                    padding: "2px 8px",
                    borderRadius: "4px",
                    background: inc.state === "resolved" ? "#d4edda" : inc.state === "failed" ? "#f8d7da" : "#fff3cd",
                    fontSize: "12px",
                  }}>
                    {inc.state}
                  </span>
                </td>
                <td style={{ padding: "8px" }}>{inc.severity || "—"}</td>
                <td style={{ padding: "8px" }}>{inc.current_stage || "—"}</td>
                <td style={{ padding: "8px" }}>{new Date(inc.created_at).toLocaleTimeString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

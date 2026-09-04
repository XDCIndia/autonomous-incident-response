"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import { buildReport, clockLabel, loadStored, nextIncidentId, persistIncident } from "@/lib/incidents";
import { HEALTHY_ALL, scenarioMap } from "@/lib/scenarios";
import type {
  EnginePhase,
  IncidentSummary,
  LogEntry,
  LogTone,
  PostMortemReport,
  Scenario,
  SimulationState,
} from "@/lib/types";

const RUNNING_PHASES: EnginePhase[] = [
  "detect",
  "triage",
  "investigate",
  "rca",
  "awaiting_approval",
  "executing",
  "escalated",
  "verify",
];

function standby(): SimulationState {
  return {
    phase: "standby",
    incident: null,
    stageIdx: -1,
    stagesDone: [false, false, false, false, false, false],
    nodeHealth: { ...HEALTHY_ALL },
    pulses: [],
    rcNode: null,
    rcShown: false,
    rca: null,
    signals: [],
    signalsShown: 0,
    findings: [],
    findingsShown: 0,
    log: [],
    confidence: null,
    proposal: null,
    proposalStatus: "pending",
    approvalCountdown: 0,
    checks: [],
    checksShown: 0,
    report: null,
    elapsedMs: 0,
    error: null,
  };
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export function useSimulation() {
  const [, force] = useReducer((c: number) => c + 1, 0);
  const S = useRef<SimulationState>(standby());
  const runToken = useRef(0);
  const logSeq = useRef(0);
  const decisionRef = useRef<"approved" | "rejected" | null>(null);
  const escalationResolve = useRef<(() => void) | null>(null);

  const commit = useCallback(() => force(), []);

  const stillRunning = useCallback((token: number) => token === runToken.current, []);

  const pushLog = useCallback(
    (stage: string, msg: string, tone: LogTone) => {
      const entry: LogEntry = {
        id: ++logSeq.current,
        t: clockLabel(),
        stage,
        msg,
        tone,
      };
      S.current.log.push(entry);
      commit();
    },
    [commit]
  );

  /** run `fn` (a state mutation), wait `ms`, abort if a newer run started */
  const beat = useCallback(
    async (token: number, ms: number, fn?: () => void): Promise<boolean> => {
      if (!stillRunning(token)) return false;
      if (fn) {
        fn();
        commit();
      }
      await sleep(ms);
      return stillRunning(token);
    },
    [stillRunning, commit]
  );

  const reset = useCallback(() => {
    runToken.current += 1;
    decisionRef.current = null;
    escalationResolve.current = null;
    S.current = standby();
    commit();
  }, [commit]);

  const start = useCallback(
    async (scenarioKey: string) => {
      const scenario = scenarioMap[scenarioKey] ?? scenarioMap.bad_deployment;
      const token = ++runToken.current;
      decisionRef.current = null;

      const startedAt = new Date();
      const stored = loadStored();
      const id = nextIncidentId(
        Math.max(stored.length, Number.parseInt(idSuffixOf(stored[0]?.id), 10) || 0)
      );

      S.current = standby();
      const st = S.current;
      st.phase = "detect";
      st.stageIdx = 0;
      st.stagesDone = [false, false, false, false, false, false];
      st.signals = scenario.detectSignals;
      const incident: IncidentSummary = {
        id,
        scenarioKey: scenario.key,
        severity: scenario.severity,
        autonomy: scenario.autonomy,
        serviceName: scenario.failedNode,
        headline: scenario.headline,
        startedAt: startedAt.toISOString(),
        status: "open",
      };
      st.incident = incident;
      commit();

      pushLog("DETECT", `Anomaly scanner tripped on ${scenario.failedNode}`, "crit");
      pushLog("DETECT", `${scenario.severity} INCIDENT OPENED · ${id} · ${scenario.autonomy} mode`, "system");
      st.stageIdx = 0;

      // DETECT — stream raw telemetry signals
      for (let i = 0; i < scenario.detectSignals.length; i++) {
        const sig = scenario.detectSignals[i];
        const proceed = await beat(token, 720, () => {
          st.signalsShown = i + 1;
          pushLog(
            "DETECT",
            `[${sig.source}] ${sig.metric} ${sig.value} — ${sig.note}`,
            sig.tone
          );
        });
        if (!proceed) return;
      }
      st.confidence = 0.97;
      pushLog("DETECT", `Detection confidence 97% — opening autonomous response`, "info");
      applyBeat(st, scenario, "detect");
      commit();
      if (!(await beat(token, 800))) return;

      // TRIAGE
      st.stageIdx = 1;
      st.stagesDone[0] = true;
      commit();
      pushLog("TRIAGE", "Correlating blast radius across the dependency graph", "info");
      if (!(await beat(token, 1100))) return;
      applyBeat(st, scenario, "triage");
      const blast = Object.entries(st.nodeHealth)
        .filter(([, h]) => h !== "healthy")
        .map(([k]) => k)
        .join(", ");
      pushLog("TRIAGE", `Blast radius: ${blast}`, "warn");
      pushLog("TRIAGE", `Severity ${scenario.severity} · autonomy ${scenario.autonomy}`, "warn");
      if (!(await beat(token, 1600))) return;
      pushLog("TRIAGE", scenario.triageSummary, "warn");
      if (!(await beat(token, 1400))) return;

      // INVESTIGATE
      st.stageIdx = 2;
      st.stagesDone[1] = true;
      commit();
      applyBeat(st, scenario, "investigate");
      pushLog("INVESTIGATE", "Dispatching log + metric investigators (parallel)", "info");
      if (!(await beat(token, 900))) return;
      for (let i = 0; i < scenario.findings.length; i++) {
        const finding = scenario.findings[i];
        const proceed = await beat(token, 900, () => {
          st.findingsShown = i + 1;
          st.confidence = Math.round(finding.confidence * 100);
          pushLog("INVESTIGATE", `${finding.agent}: ${finding.headline}`, "info");
        });
        if (!proceed) return;
        if (!(await beat(token, i === scenario.findings.length - 1 ? 500 : 1500))) return;
      }
      st.findingsShown = scenario.findings.length;
      commit();
      if (!(await beat(token, 500))) return;

      // RCA
      st.stageIdx = 3;
      st.stagesDone[2] = true;
      st.rcNode = scenario.rcNode;
      st.rcShown = true;
      st.rca = scenario.rca;
      st.confidence = scenario.rca.confidencePct;
      commit();
      pushLog("RCA", `Root cause isolated — ${scenario.rca.headline}`, "ok");
      if (!(await beat(token, 2600))) return;
      pushLog("RCA", `Confidence ${scenario.rca.confidencePct}% — correlating with graph topology`, "ok");
      if (!(await beat(token, 1200))) return;

      // REMEDIATE (proposal + approval)
      st.stageIdx = 4;
      st.stagesDone[3] = true;
      st.proposal = scenario.remediation;
      st.proposalStatus = "pending";
      st.phase = "awaiting_approval";
      st.approvalCountdown = scenario.remediation.requiresApproval ? 9 : 0;
      decisionRef.current = null;
      commit();
      pushLog(
        "REMEDIATE",
        `Proposal: ${scenario.remediation.action} on ${scenario.remediation.target} · ${scenario.remediation.risk} risk`,
        "warn"
      );
      pushLog("REMEDIATE", scenario.remediation.requiresApproval ? "Awaiting operator approval…" : "No approval required — proceeding", "info");
      if (scenario.remediation.requiresApproval) {
        for (let n = 9; n > 0; n--) {
          if (decisionRef.current) break;
          if (!(await beat(token, 1000, () => (st.approvalCountdown = n - 1)))) return;
        }
        const decision: "approved" | "rejected" =
          (decisionRef.current as "approved" | "rejected" | null) ?? "approved"; // autonomous auto-approve
        if (decision === "rejected") {
          st.proposalStatus = "rejected";
          st.phase = "escalated";
          commit();
          pushLog("REMEDIATE", "Operator REJECTED auto-remediation — escalating to on-call", "crit");
          await new Promise<void>((resolve) => {
            escalationResolve.current = resolve;
          });
          if (!stillRunning(token)) return;
          st.phase = "awaiting_approval";
          st.proposalStatus = "approved";
          pushLog("REMEDIATE", "Manual runbook confirmed — applying remediation", "ok");
          commit();
        } else {
          st.proposalStatus = "approved";
          pushLog("REMEDIATE", "Approval received — applying remediation", "ok");
          commit();
        }
      }
      // executing
      st.phase = "executing";
      commit();
      for (const step of scenario.remediation.steps) {
        if (!(await beat(token, 950, () => pushLog("REMEDIATE", `▸ ${step}`, "info")))) return;
      }
      st.proposalStatus = "done";
      pushLog("REMEDIATE", `${scenario.remediation.action} complete on ${scenario.remediation.target}`, "ok");
      if (!(await beat(token, 800))) return;

      // VERIFY
      st.stageIdx = 5;
      st.stagesDone[4] = true;
      st.checks = scenario.verifyChecks;
      st.checksShown = 0;
      st.pulses = [];
      st.phase = "verify";
      // heal the graph: everything returns to nominal
      for (const key of Object.keys(st.nodeHealth)) st.nodeHealth[key] = "healthy";
      commit();
      pushLog("VERIFY", "Remediation applied — running verification checks", "info");
      if (!(await beat(token, 900))) return;
      for (let i = 0; i < scenario.verifyChecks.length; i++) {
        const check = scenario.verifyChecks[i];
        const proceed = await beat(token, 800, () => {
          st.checksShown = i + 1;
          pushLog("VERIFY", `✓ ${check.label} — ${check.detail}`, "ok");
        });
        if (!proceed) return;
      }
      st.stagesDone[5] = true;
      st.stageIdx = -1;
      commit();
      if (!(await beat(token, 700))) return;

      // RESOLVED
      const resolvedAt = new Date();
      const durationSec = Math.max(1, Math.round((resolvedAt.getTime() - startedAt.getTime()) / 1000));
      const report: PostMortemReport = buildReport(scenario, {
        id,
        startedAt: startedAt.toISOString(),
        resolvedAt: resolvedAt.toISOString(),
        durationSec,
        timeline: S.current.log.map((e) => ({ t: e.t, stage: e.stage, msg: e.msg, tone: e.tone })),
      });
      persistIncident(report);
      st.report = report;
      st.incident = {
        ...incident,
        resolvedAt: resolvedAt.toISOString(),
        durationSec,
        status: "resolved",
      };
      st.phase = "resolved";
      st.rcShown = false;
      st.rcNode = null;
      pushLog("RESOLVED", `Incident resolved in ${formatSec(durationSec)} — telemetry nominal`, "ok");
      pushLog("RESOLVED", "AI core re-armed · post-mortem generated", "system");
      commit();
    },
    [pushLog, beat, commit]
  );

  // live elapsed clock while running
  useEffect(() => {
    let last = Date.now();
    const iv = window.setInterval(() => {
      const now = Date.now();
      const delta = now - last;
      last = now;
      if (!RUNNING_PHASES.includes(S.current.phase)) return;
      S.current.elapsedMs += delta;
      commit();
    }, 1000);
    return () => window.clearInterval(iv);
  }, [commit]);

  const approve = useCallback(() => {
    if (S.current.phase !== "awaiting_approval") return;
    if (S.current.proposalStatus !== "pending") return;
    decisionRef.current = "approved";
    pushLog("REMEDIATE", "Operator approval received ✓", "ok");
  }, [pushLog]);

  const reject = useCallback(() => {
    if (S.current.phase !== "awaiting_approval") return;
    if (S.current.proposalStatus !== "pending") return;
    decisionRef.current = "rejected";
    pushLog("REMEDIATE", "Operator rejected proposal ✗", "crit");
  }, [pushLog]);

  const confirmManual = useCallback(() => {
    if (S.current.phase !== "escalated") return;
    escalationResolve.current?.();
    escalationResolve.current = null;
  }, []);

  return { sim: S.current, start, approve, reject, confirmManual, reset };
}

/* ------------------------------------------------------------------ */

function applyBeat(
  st: SimulationState,
  scenario: Scenario,
  stage: "detect" | "triage" | "investigate"
) {
  const spread = scenario.spread.find((b) => b.when === stage);
  if (!spread) return;
  if (spread.set) {
    for (const [k, v] of Object.entries(spread.set)) st.nodeHealth[k] = v;
  }
  st.pulses = spread.pulses ?? [];
}

function idSuffixOf(id: string | undefined): string {
  if (!id) return "0";
  const m = id.match(/-(\d+)$/);
  return m ? m[1] : "0";
}

function formatSec(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

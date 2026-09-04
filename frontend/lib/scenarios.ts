import type {
  EdgeSpec,
  GraphNode,
  Scenario,
} from "./types";

/* ------------------------------------------------------------------ *
 *  System topology — a small e-commerce control plane
 * ------------------------------------------------------------------ */

export const NODES: GraphNode[] = [
  {
    key: "api-gateway",
    label: "api-gateway",
    role: "edge",
    x: 520,
    y: 82,
    calls: ["auth-service", "order-service", "payment-service"],
    blurb: "Edge router — terminates client traffic, auth, rate limits and routes to services.",
  },
  {
    key: "auth-service",
    label: "auth-service",
    role: "service",
    x: 175,
    y: 258,
    calls: ["user-db"],
    blurb: "Login, tokens and RBAC. Every request passes through here.",
  },
  {
    key: "user-db",
    label: "user-db",
    role: "data",
    x: 175,
    y: 470,
    calls: [],
    blurb: "Postgres 15 — users, sessions and credentials.",
  },
  {
    key: "order-service",
    label: "order-service",
    role: "service",
    x: 520,
    y: 258,
    calls: ["payment-service", "inventory-service"],
    blurb: "Checkout orchestration — places orders, charges cards, reserves stock.",
  },
  {
    key: "payment-service",
    label: "payment-service",
    role: "service",
    x: 865,
    y: 258,
    calls: ["billing-db", "payments-provider"],
    blurb: "Card charging, refunds and settlement via the external payments provider.",
  },
  {
    key: "billing-db",
    label: "billing-db",
    role: "data",
    x: 742,
    y: 470,
    calls: [],
    blurb: "Postgres 15 — ledger, transactions and invoices.",
  },
  {
    key: "payments-provider",
    label: "payments-provider",
    role: "external",
    x: 988,
    y: 470,
    calls: [],
    blurb: "External SaaS gateway — card network rails. Monitored, not controlled.",
  },
  {
    key: "inventory-service",
    label: "inventory-service",
    role: "service",
    x: 330,
    y: 470,
    calls: [],
    blurb: "Stock reservations and availability checks during checkout.",
  },
];

export const nodeMap: Record<string, GraphNode> = Object.fromEntries(
  NODES.map((n) => [n.key, n])
);

export const EDGES: EdgeSpec[] = NODES.flatMap((n) =>
  n.calls.map((to) => ({
    from: n.key,
    to,
    external: nodeMap[to]?.role === "external",
  }))
);

/** upstream callers that depend on `key` */
export function dependentsOf(key: string): string[] {
  return NODES.filter((n) => n.calls.includes(key)).map((n) => n.key);
}

export const HEALTHY_ALL: Record<string, "healthy"> = Object.fromEntries(
  NODES.map((n) => [n.key, "healthy"])
) as Record<string, "healthy">;

/* ------------------------------------------------------------------ *
 *  Scenarios
 * ------------------------------------------------------------------ */

export const SCENARIOS: Scenario[] = [
  {
    key: "bad_deployment",
    label: "Bad Deploy",
    tagline: "v2.4.1 ships a faulty payment handler",
    chip: { text: "text-rose-400", dot: "bg-rose-400" },
    featured: true,
    severity: "P1",
    autonomy: "AUTONOMOUS",
    failedNode: "payment-service",
    rcNode: "payment-service",
    headline: "Error-rate surge on payment-service after deploy v2.4.1",
    detectSummary:
      "Telemetry step-change on payment-service: error_rate 0.2% → 45.1%, p95 latency → 2450ms, timestamped 90s after deploy v2.4.1.",
    detectSignals: [
      {
        source: "payment-service",
        metric: "deploy",
        value: "v2.4.1",
        tone: "warn",
        note: "rollout completed 07:42:09Z",
      },
      {
        source: "payment-service",
        metric: "error_rate",
        value: "45.1%",
        tone: "crit",
        note: "baseline 0.2% — 500s on POST /charge",
      },
      {
        source: "payment-service",
        metric: "p95_latency",
        value: "2450ms",
        tone: "crit",
        note: "baseline 210ms",
      },
      {
        source: "payment-service",
        metric: "log_error",
        value: "NPE",
        tone: "crit",
        note: "NullPointerException in PaymentHandler.process",
      },
    ],
    triageSummary:
      "Checkout path is failing for a P1 share of traffic. charge calls error 45%, order-service and api-gateway show elevated latency downstream.",
    spread: [
      {
        when: "detect",
        set: { "payment-service": "down" },
        pulses: [],
      },
      {
        when: "triage",
        set: {
          "order-service": "degraded",
          "api-gateway": "degraded",
        },
        pulses: [
          ["payment-service", "order-service"],
          ["payment-service", "api-gateway"],
        ],
      },
    ],
    findings: [
      {
        agent: "LOG-INVESTIGATOR",
        headline: "NPE stack trace references code shipped in v2.4.1",
        detail:
          "PaymentHandler.process (PaymentHandler.java:42) dereferences a null capture context. Line 42 was introduced in v2.4.1 — absent from v2.4.0.",
        evidence: [
          "07:42:31 ERROR NullPointerException at PaymentHandler.process:42",
          "07:42:31 capturedContext=null after provider callback",
          "git blame: PaymentHandler.java:42 added in v2.4.1",
        ],
        confidence: 0.82,
      },
      {
        agent: "METRIC-INVESTIGATOR",
        headline: "Error/latency step-change correlates with deploy timestamp",
        detail:
          "error_rate and p95 latency both step at 07:42:09Z (±3s) — the exact deploy of v2.4.1. No DB or upstream anomaly precedes it.",
        evidence: [
          "error_rate 0.2% → 45.1% at 07:42:09Z",
          "p95 latency 210ms → 2450ms at 07:42:09Z",
          "billing-db & payments-provider flat during window",
        ],
        confidence: 0.9,
      },
      {
        agent: "ARBITER",
        headline: "No conflict — both signals point at v2.4.1",
        detail:
          "Log and metric evidence agree within the deploy window. Root cause is code, not infrastructure.",
        evidence: ["hypothesis agreement 100%", "conflict: none"],
        confidence: 0.94,
      },
    ],
    rca: {
      headline: "payment-service v2.4.1 — faulty PaymentHandler code path",
      narrative:
        "A null capture context introduced in v2.4.1 throws on every charge callback. The deploy window matches the telemetry step-change exactly, and billing-db + payments-provider were stable — isolating the defect to the service image.",
      evidence: [
        "deploy v2.4.1 at 07:42:09Z",
        "error_rate step 0.2% → 45.1% at deploy +90s",
        "NPE references PaymentHandler.java:42 (new in v2.4.1)",
        "no infrastructure anomaly in window",
      ],
      confidencePct: 94,
    },
    remediation: {
      action: "ROLLBACK DEPLOY",
      target: "payment-service",
      risk: "LOW",
      description:
        "Roll the service back to the last-known-good image. Traffic is drained before cutover so no in-flight charge is lost.",
      rollbackInfo:
        "v2.4.1 → v2.4.0 · artifact sha 9f2c1da · canary-gated · full undo in one action",
      steps: [
        "Drain payment-service traffic at gateway",
        "Roll back image v2.4.1 → v2.4.0",
        "Warm connection pools & clear circuit state",
        "Resume traffic 10% → 100% with alert watch",
      ],
      requiresApproval: true,
    },
    verifyChecks: [
      { label: "error_rate < 0.5%", detail: "measured 0.21%" },
      { label: "p95 latency < 300ms", detail: "measured 228ms" },
      { label: "success_rate ≥ 99.5%", detail: "measured 99.92%" },
      { label: "HTTP 500s last 2m = 0", detail: "measured 0" },
    ],
    report: {
      rootCause:
        "payment-service v2.4.1 introduced a null capture context in PaymentHandler.process (line 42), crashing every charge callback.",
      impact:
        "45% of charge calls failed for ~11 minutes. order-service and api-gateway degraded; roughly 2,300 checkout attempts affected.",
      actionTaken:
        "Auto-rollback to v2.4.0 with traffic drain/cutover; pools warmed and traffic re-ramped under alert watch.",
      prevention: [
        "Progressive canary rollout with auto-rollback gate at error_rate > 1%",
        "Regression test covering PaymentHandler.process null-context path",
        "Error-budget alert raised before deploy promotion is allowed",
      ],
      metrics: [
        { label: "error_rate", before: "45.1%", after: "0.21%", ok: true },
        { label: "p95 latency", before: "2450ms", after: "228ms", ok: true },
        { label: "success_rate", before: "54.9%", after: "99.92%", ok: true },
      ],
    },
  },
  {
    key: "dependency_outage",
    label: "Dependency Outage",
    tagline: "External payments provider goes dark",
    chip: { text: "text-amber-400", dot: "bg-amber-400" },
    severity: "P2",
    autonomy: "SEMI-AUTONOMOUS",
    failedNode: "payments-provider",
    rcNode: "payment-service",
    headline: "Circuit breaker OPEN — payments-provider unreachable",
    detectSummary:
      "payments-provider stopped answering: TCP connect timeout, HTTP 504s, then payment-service opened its circuit breaker and fell back to queuing.",
    detectSignals: [
      {
        source: "payments-provider",
        metric: "tcp_connect",
        value: "timeout",
        tone: "crit",
        note: "3/3 regions unreachable",
      },
      {
        source: "payments-provider",
        metric: "http_504",
        value: "100%",
        tone: "crit",
        note: "gateway endpoint dead",
      },
      {
        source: "payment-service",
        metric: "circuit",
        value: "OPEN",
        tone: "warn",
        note: "breaker tripped after 10 consecutive failures",
      },
      {
        source: "payment-service",
        metric: "fallback",
        value: "failed",
        tone: "warn",
        note: "queue depth rising — charges deferred",
      },
    ],
    triageSummary:
      "External dependency failure, not a service defect. Charge calls are deferred (P2 blast radius): payment-service degraded, order-service latency up.",
    spread: [
      {
        when: "detect",
        set: { "payments-provider": "down" },
        pulses: [],
      },
      {
        when: "triage",
        set: { "payment-service": "degraded" },
        pulses: [["payments-provider", "payment-service"]],
      },
      {
        when: "investigate",
        set: { "order-service": "degraded" },
        pulses: [
          ["payments-provider", "payment-service"],
          ["payment-service", "order-service"],
        ],
      },
    ],
    findings: [
      {
        agent: "LOG-INVESTIGATOR",
        headline: "Initial suspicion: payment-service retry storm",
        detail:
          "Thousands of 'provider call failed — retrying' lines. Pattern looked internal until stack inspection showed connect timeouts, not app errors.",
        evidence: [
          "07:58:11 WARN provider call failed — retry 3/5",
          "07:58:14 ERROR ConnectTimeoutException to api.provider.example",
        ],
        confidence: 0.6,
      },
      {
        agent: "METRIC-INVESTIGATOR",
        headline: "Provider reachability is the only moving signal",
        detail:
          "Outbound connect timeout 100%, TCP handshake 0% across all payment pods. payment-service CPU/memory flat — it is waiting on the wire, not leaking.",
        evidence: [
          "provider tcp_connect success 0% at 07:57",
          "payment-service heap flat (41% → 43%)",
          "upstream latency ∞ (timeout) — not p95 shift",
        ],
        confidence: 0.88,
      },
      {
        agent: "ARBITER",
        headline: "Conflict resolved in favour of dependency outage",
        detail:
          "Log volume is a retry artifact of the outage. The connect-timeout signature and flat internal metrics override the initial internal-fault hypothesis.",
        evidence: ["initial hypothesis: internal defect", "resolved: external outage"],
        confidence: 0.88,
      },
    ],
    rca: {
      headline: "payments-provider unreachable — external outage",
      narrative:
        "payment-service is healthy but cannot reach its upstream provider — TCP connect fails in all regions. The retry storm is a symptom. Correct response is isolation: open the breaker and shift to the standby provider.",
      evidence: [
        "provider tcp_connect success 0% (all regions)",
        "payment-service heap & CPU flat during window",
        "breaker OPEN after 10 consecutive failures",
        "no deploy or config change in window",
      ],
      confidencePct: 88,
    },
    remediation: {
      action: "CIRCUIT BREAK + FAILOVER",
      target: "payment-service",
      risk: "MEDIUM",
      description:
        "Keep the breaker open, shift charge traffic to the standby provider, and drain the deferred queue once the primary is verified.",
      rollbackInfo:
        "Standby: pay-fallback.example (contract-mocked) · revert = close circuit after 60s of clean probes · full undo in one action",
      steps: [
        "Confirm breaker OPEN on payments-provider",
        "Point provider DNS to standby pay-fallback",
        "Backpressure 30% of traffic during warm-up",
        "Replay deferred queue after 60s clean window",
      ],
      requiresApproval: true,
    },
    verifyChecks: [
      { label: "provider probe OK", detail: "standby returns 200 in <120ms" },
      { label: "circuit state CLOSED", detail: "breaker reset after clean window" },
      { label: "error_rate < 2%", detail: "measured 0.9%" },
      { label: "deferred queue drained", detail: "0 pending charges" },
    ],
    report: {
      rootCause:
        "External payments-provider outage: TCP connects failed in all regions. Not a payment-service defect — confirmed by flat internal metrics and connect-timeout signature.",
      impact:
        "~8 minutes of deferred charge processing. payment-service and order-service degraded; no data loss — charges queued and replayed.",
      actionTaken:
        "Breaker kept open, traffic failed over to the standby provider, deferred queue replayed after a 60s clean probe window.",
      prevention: [
        "Synthetic health probes to payments-provider every 30s from 3 regions",
        "Quarterly failover rehearsal to the standby provider",
        "Keep 30% headroom on the deferred-queue replay path",
      ],
      metrics: [
        { label: "provider availability", before: "0%", after: "100%", ok: true },
        { label: "error_rate", before: "9.4%", after: "0.9%", ok: true },
        { label: "deferred queue", before: "1,240", after: "0", ok: true },
      ],
    },
  },
  {
    key: "resource_exhaustion",
    label: "Resource Exhaustion",
    tagline: "Memory leak starves order-service",
    chip: { text: "text-cyan-400", dot: "bg-cyan-400" },
    severity: "P2",
    autonomy: "SEMI-AUTONOMOUS",
    failedNode: "order-service",
    rcNode: "order-service",
    headline: "order-service OOM risk — heap 92%, GC pauses rising",
    detectSummary:
      "order-service degraded gradually: CPU 95%, heap 92% with OOM warnings and 1.4s GC pauses. Latency crept 200ms → 1800ms over two hours.",
    detectSignals: [
      {
        source: "order-service",
        metric: "cpu_usage",
        value: "95%",
        tone: "warn",
        note: "gradual climb over 2h",
      },
      {
        source: "order-service",
        metric: "heap_usage",
        value: "92%",
        tone: "crit",
        note: "OOM kill imminent",
      },
      {
        source: "order-service",
        metric: "gc_pause",
        value: "1.4s",
        tone: "warn",
        note: "baseline 80ms",
      },
      {
        source: "order-service",
        metric: "p95_latency",
        value: "1800ms",
        tone: "warn",
        note: "baseline 200ms",
      },
    ],
    triageSummary:
      "Slow-burn resource exhaustion on the checkout path. Order placement degraded; no customer data at risk yet, but OOM kill would drop in-flight orders.",
    spread: [
      {
        when: "detect",
        set: { "order-service": "down" },
        pulses: [],
      },
      {
        when: "triage",
        set: { "api-gateway": "degraded" },
        pulses: [["order-service", "api-gateway"]],
      },
    ],
    findings: [
      {
        agent: "LOG-INVESTIGATOR",
        headline: "Retained cart snapshots never released",
        detail:
          "OrderContext holds a reference to the full cart snapshot for the lifetime of the request object, which the retry cache keeps alive for 30 minutes.",
        evidence: [
          "WARN heap 92% — GC young-gen churn high",
          "OrderContext.retain(cartSnapshot) never released",
          "retry-cache TTL 30m pins stale contexts",
        ],
        confidence: 0.8,
      },
      {
        agent: "METRIC-INVESTIGATOR",
        headline: "Linear heap growth — leak, not load",
        detail:
          "Heap grows monotonically even at flat request rate (420 rps ±5%). GC pause time doubles every ~25 minutes — classic retention leak signature.",
        evidence: [
          "heap 58% → 92% linear at flat rps",
          "GC pause 80ms → 1.4s, young-gen churn 3x",
          "rps flat 420 — not a traffic problem",
        ],
        confidence: 0.87,
      },
      {
        agent: "ARBITER",
        headline: "Agreement — retention leak in OrderContext",
        detail:
          "Logs identify the retained reference; metrics confirm a leak (not load). Both point at order-service's cart-snapshot retention.",
        evidence: ["hypothesis agreement 100%", "conflict: none"],
        confidence: 0.91,
      },
    ],
    rca: {
      headline: "order-service memory leak — cart snapshots pinned in retry cache",
      narrative:
        "OrderContext pins full cart snapshots for the request lifetime, and the 30-minute retry cache keeps them alive. Heap growth is monotonic at flat traffic — a leak, not load. OOM was minutes away.",
      evidence: [
        "heap 58% → 92% at flat 420 rps",
        "GC pause 80ms → 1.4s over 2h",
        "OrderContext retains cartSnapshot for request lifetime",
        "retry-cache TTL 30m pins stale contexts",
      ],
      confidencePct: 91,
    },
    remediation: {
      action: "SCALE-OUT + ROLLING RESTART",
      target: "order-service",
      risk: "LOW",
      description:
        "Scale replicas 4 → 8 and rolling-restart to shed the leaked heap before the OOM kill. Capture a heap profile during drain for the follow-up fix.",
      rollbackInfo:
        "replicas 8 → 4 anytime · restart is stateless (in-flight orders re-queued) · full undo in one action",
      steps: [
        "Scale order-service 4 → 8 replicas",
        "Rolling restart with 2-minute drain per pod",
        "Capture heap profile during drain",
        "Lower autoscaler CPU headroom to 20%",
      ],
      requiresApproval: true,
    },
    verifyChecks: [
      { label: "cpu_usage < 70%", detail: "measured 52%" },
      { label: "heap_usage < 75%", detail: "measured 61%" },
      { label: "p95 latency < 400ms", detail: "measured 312ms" },
      { label: "OOM kills = 0", detail: "measured 0" },
    ],
    report: {
      rootCause:
        "Memory leak in order-service: OrderContext pins full cart snapshots and the 30-minute retry cache keeps them alive. Heap grew monotonically at flat traffic.",
      impact:
        "Checkout latency degraded 200ms → 1800ms over 2 hours. No data loss; OOM kill would have dropped in-flight orders within ~20 minutes.",
      actionTaken:
        "Scaled 4 → 8 replicas and rolling-restarted to shed leaked heap. Heap profile captured for the code fix; autoscaler headroom lowered.",
      prevention: [
        "Retention-leak detector alerting on monotonic heap growth",
        "Null the OrderContext reference after request completion",
        "Shorten retry-cache TTL and cap cart snapshot size",
      ],
      metrics: [
        { label: "heap_usage", before: "92%", after: "61%", ok: true },
        { label: "p95 latency", before: "1800ms", after: "312ms", ok: true },
        { label: "GC pause", before: "1.4s", after: "95ms", ok: true },
      ],
    },
  },
  {
    key: "database_failure",
    label: "DB Failure",
    tagline: "user-db connection pool exhausted",
    chip: { text: "text-violet-400", dot: "bg-violet-400" },
    severity: "P1",
    autonomy: "AUTONOMOUS",
    failedNode: "user-db",
    rcNode: "auth-service",
    headline: "Login storm — user-db pool exhausted 100/100",
    detectSummary:
      "user-db connection pool is saturated (100/100) with 5s acquire timeouts. auth-service is failing login, and every route is 401-ing as sessions cannot be validated.",
    detectSignals: [
      {
        source: "user-db",
        metric: "pool_usage",
        value: "100/100",
        tone: "crit",
        note: "all connections held",
      },
      {
        source: "user-db",
        metric: "acquire_timeout",
        value: "5s",
        tone: "crit",
        note: "queries queued then dropped",
      },
      {
        source: "auth-service",
        metric: "login_failures",
        value: "87%",
        tone: "crit",
        note: "sessions cannot validate",
      },
      {
        source: "api-gateway",
        metric: "http_401",
        value: "surge",
        tone: "warn",
        note: "token validation timing out",
      },
    ],
    triageSummary:
      "Database failure with total blast radius — auth is upstream of every request. api-gateway, order-service and payment-service all degrade as session validation fails.",
    spread: [
      {
        when: "detect",
        set: { "user-db": "down" },
        pulses: [],
      },
      {
        when: "triage",
        set: { "auth-service": "down" },
        pulses: [["user-db", "auth-service"]],
      },
      {
        when: "investigate",
        set: {
          "api-gateway": "degraded",
          "order-service": "degraded",
          "payment-service": "degraded",
        },
        pulses: [
          ["user-db", "auth-service"],
          ["auth-service", "api-gateway"],
          ["auth-service", "order-service"],
          ["auth-service", "payment-service"],
        ],
      },
    ],
    findings: [
      {
        agent: "LOG-INVESTIGATOR",
        headline: "Pool exhausted — connections held, not leaking",
        detail:
          "100/100 connections checked out for 5+ seconds each. Slow queries from a new report job (auth_audit_export) hold connections open and starve login traffic.",
        evidence: [
          "ERROR ConnectionPoolTimeoutException — 100/100 in use",
          "auth_audit_export running 14m, 0 rows returned",
          "no connection leak — all held by slow query",
        ],
        confidence: 0.79,
      },
      {
        agent: "METRIC-INVESTIGATOR",
        headline: "Audit export correlates with pool saturation",
        detail:
          "Pool saturation began at 07:51 when auth_audit_export started; it holds 40+ connections on a seq-scan over the full audit table. Login query time follows pool pressure exactly.",
        evidence: [
          "pool 100% since 07:51 (export start)",
          "audit export holds 44 connections, seq scan",
          "login p95 60ms → 5s timeout in lockstep",
        ],
        confidence: 0.86,
      },
      {
        agent: "ARBITER",
        headline: "Agreement — runaway query, not capacity",
        detail:
          "The database has headroom (CPU 30%, no locks); a single unindexed export query starved the pool. Both investigators converge on connection starvation by auth_audit_export.",
        evidence: ["hypothesis agreement 100%", "conflict: none"],
        confidence: 0.9,
      },
    ],
    rca: {
      headline: "user-db pool starved by unindexed auth_audit_export",
      narrative:
        "A manually-run audit export seq-scans the full audit table and holds ~44 pool connections. Login traffic starves: 100/100 saturated, 5s acquire timeouts, then mass 401s as sessions fail to validate. Auth is upstream of every route, so the blast radius is total.",
      evidence: [
        "pool 100/100 saturated since 07:51",
        "auth_audit_export holds 44 connections, no index",
        "login p95 60ms → 5s acquire timeout",
        "401 surge at api-gateway — sessions unvalidatable",
      ],
      confidencePct: 90,
    },
    remediation: {
      action: "RESET CONNECTION POOL",
      target: "user-db",
      risk: "HIGH",
      description:
        "Kill the runaway export, reset the pool and raise the ceiling. Traffic is throttled at the gateway for the first 60s so the pool recovers cleanly.",
      rollbackInfo:
        "config restore via db-operator · pool 100 → 250, acquire timeout 30s → 5s · revert at first new alarm",
      steps: [
        "Throttle auth-bound traffic at api-gateway",
        "Cancel auth_audit_export and kill its connections",
        "Reset pool, raise ceiling 100 → 250",
        "Release throttle and watch login p95",
      ],
      requiresApproval: true,
    },
    verifyChecks: [
      { label: "pool_usage < 60%", detail: "measured 34%" },
      { label: "auth p95 latency < 200ms", detail: "measured 88ms" },
      { label: "pool timeouts = 0", detail: "measured 0" },
      { label: "gateway 401 rate baseline", detail: "measured 0.3%" },
    ],
    report: {
      rootCause:
        "A manually-run auth_audit_export seq-scanned the full audit table and held ~44 pool connections, starving login traffic on user-db (100/100, 5s acquire timeouts).",
      impact:
        "Full auth outage ~6 minutes — every route 401'd as sessions could not be validated. Login failures at 87%; no data corruption.",
      actionTaken:
        "Cancelled the runaway export, reset the pool, raised the ceiling 100 → 250 and released the gateway throttle under alert watch.",
      prevention: [
        "Index auth_audit table and gate exports behind a reporting replica",
        "Per-query connection budget so one query cannot starve the pool",
        "Runbook alert when pool usage exceeds 80% for 60s",
      ],
      metrics: [
        { label: "pool_usage", before: "100/100", after: "34/250", ok: true },
        { label: "auth p95 latency", before: "5s timeout", after: "88ms", ok: true },
        { label: "login success", before: "13%", after: "99.7%", ok: true },
      ],
    },
  },
];

export const scenarioMap: Record<string, Scenario> = Object.fromEntries(
  SCENARIOS.map((s) => [s.key, s])
);

export const SCENARIO_KEYS: string[] = SCENARIOS.map((s) => s.key);

export function randomScenarioKey(): string {
  return SCENARIO_KEYS[Math.floor(Math.random() * SCENARIO_KEYS.length)];
}

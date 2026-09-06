"use client";

import { useEffect, useRef, useState } from "react";

/* ── Types ── */

interface LogLine {
  id: number;
  stage: "detect" | "investigate" | "rca" | "fix" | "verify" | "system";
  text: string;
  detail?: string;
  status?: "ok" | "warn" | "crit" | "info";
  timestamp: string;
}

/* ── Constants ── */

const STAGE_COLORS: Record<LogLine["stage"], string> = {
  detect: "text-[var(--color-accent-cyan)]",
  investigate: "text-[var(--color-accent-purple)]",
  rca: "text-[var(--color-accent-amber)]",
  fix: "text-[var(--color-accent-red)]",
  verify: "text-[var(--color-status-healthy)]",
  system: "text-[var(--color-text-muted)]",
};

const STAGE_LABELS: Record<LogLine["stage"], string> = {
  detect: "DETECT",
  investigate: "INVESTIGATE",
  rca: "RCA",
  fix: "REMEDIATE",
  verify: "VERIFY",
  system: "SYSTEM",
};

const LOG_SCRIPT: Omit<LogLine, "id" | "timestamp">[] = [
  { stage: "system", text: "Monitoring 4 services…", status: "info" },
  { stage: "detect", text: "Anomaly detected in payment-service", detail: "p99 latency 4.2s ↑ 847%", status: "crit" },
  { stage: "detect", text: "Health check failed — HTTP 503", detail: "3/3 replicas unresponsive", status: "crit" },
  { stage: "system", text: "Incident INC-2026-0904-041 created", status: "info" },
  { stage: "investigate", text: "Querying logs across 3 services…", detail: "grep 'ERROR' --since 5m", status: "info" },
  { stage: "investigate", text: "Found 47 error patterns in payment-service", detail: "NullPointerException in PaymentGateway.java:342", status: "warn" },
  { stage: "investigate", text: "Metrics correlation: DB connection pool exhausted", detail: "pool_size=10, active=10, wait_queue=34", status: "warn" },
  { stage: "rca", text: "Root cause identified with 94% confidence", detail: "v2.14.1 introduced unclosed connection leak in retry handler", status: "info" },
  { stage: "rca", text: "Blast radius: 1 service — payment-service", detail: "Severity: P1 · user-facing checkout failures", status: "warn" },
  { stage: "fix", text: "Executing safe remediation…", detail: "rollback payment-service to v2.14.0", status: "info" },
  { stage: "fix", text: "Deployment stabilized", detail: "3/3 replicas healthy · p99 180ms", status: "ok" },
  { stage: "verify", text: "Running recovery verification…", detail: "checkout_flow_e2e ✓ · payment_api_smoke ✓ · db_pool_health ✓", status: "ok" },
  { stage: "verify", text: "All 5 checks passed — incident resolved", detail: "Total time: 3m 24s", status: "ok" },
  { stage: "system", text: "Post-mortem generated and archived", status: "info" },
];

const TYPING_SPEED = 28; // ms per character
const LINE_DELAY = 350; // ms between lines
const LOOP_PAUSE = 4000; // ms before restart

/* ── Component ── */

export function TerminalMock() {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [currentText, setCurrentText] = useState("");
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isTyping, setIsTyping] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const prefersReducedMotion = useRef(false);

  useEffect(() => {
    prefersReducedMotion.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines, currentText]);

  // Typing engine
  useEffect(() => {
    if (prefersReducedMotion.current) {
      // Show all lines instantly
      const now = new Date();
      setLines(
        LOG_SCRIPT.map((line, i) => ({
          ...line,
          id: i,
          timestamp: now.toLocaleTimeString("en-US", { hour12: false }),
        }))
      );
      setIsComplete(true);
      return;
    }

    if (currentIndex >= LOG_SCRIPT.length) {
      setIsComplete(true);
      const timer = setTimeout(() => {
        setLines([]);
        setCurrentIndex(0);
        setCurrentText("");
        setIsComplete(false);
      }, LOOP_PAUSE);
      return () => clearTimeout(timer);
    }

    const current = LOG_SCRIPT[currentIndex];
    const fullText = current.text;

    if (!isTyping) {
      const startTimer = setTimeout(() => setIsTyping(true), currentIndex === 0 ? 600 : LINE_DELAY);
      return () => clearTimeout(startTimer);
    }

    if (currentText.length < fullText.length) {
      const charTimer = setTimeout(() => {
        setCurrentText(fullText.slice(0, currentText.length + 1));
      }, TYPING_SPEED);
      return () => clearTimeout(charTimer);
    }

    // Line complete
    const now = new Date();
    const newLine: LogLine = {
      ...current,
      id: currentIndex,
      timestamp: now.toLocaleTimeString("en-US", { hour12: false }),
    };

    const nextTimer = setTimeout(() => {
      setLines((prev) => [...prev, newLine]);
      setCurrentText("");
      setCurrentIndex((i) => i + 1);
      setIsTyping(false);
    }, 100);
    return () => clearTimeout(nextTimer);
  }, [currentIndex, currentText, isTyping]);

  return (
    <div className="terminal-shell">
      {/* Terminal chrome */}
      <div className="terminal-chrome">
        <div className="flex items-center gap-2">
          <span className="terminal-dot terminal-dot-red" />
          <span className="terminal-dot terminal-dot-amber" />
          <span className="terminal-dot terminal-dot-green" />
        </div>
        <span className="terminal-title">system-bachao — AI core</span>
        <span className="terminal-live">
          <span className="terminal-live-dot" />
          LIVE
        </span>
      </div>

      {/* Terminal body */}
      <div ref={scrollRef} className="terminal-body">
        {lines.map((line) => (
          <div key={line.id} className="terminal-line">
            <span className="terminal-timestamp">{line.timestamp}</span>
            <span className={`terminal-stage ${STAGE_COLORS[line.stage]}`}>
              [{STAGE_LABELS[line.stage]}]
            </span>
            <span className="terminal-text">{line.text}</span>
            {line.detail && <span className="terminal-detail">{line.detail}</span>}
          </div>
        ))}

        {currentIndex < LOG_SCRIPT.length && (
          <div className="terminal-line">
            <span className="terminal-timestamp">
              {new Date().toLocaleTimeString("en-US", { hour12: false })}
            </span>
            <span className={`terminal-stage ${STAGE_COLORS[LOG_SCRIPT[currentIndex].stage]}`}>
              [{STAGE_LABELS[LOG_SCRIPT[currentIndex].stage]}]
            </span>
            <span className="terminal-text">
              {currentText}
              <span className="terminal-cursor" />
            </span>
          </div>
        )}

        {isComplete && (
          <div className="terminal-line terminal-line-complete">
            <span className="terminal-timestamp">
              {new Date().toLocaleTimeString("en-US", { hour12: false })}
            </span>
            <span className="terminal-stage text-[var(--color-status-healthy)]">[RESOLVED]</span>
            <span className="terminal-text text-[var(--color-status-healthy)]">
              Incident closed · full audit trail available
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

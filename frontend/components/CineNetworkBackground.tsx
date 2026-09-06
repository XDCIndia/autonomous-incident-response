"use client";

import { useEffect, useRef } from "react";
import { useTheme } from "@/lib/theme-context";

// Looping animated background for the landing page only: a drifting
// cyan network (three depth layers of nodes + proximity links) with
// occasional sonar pulse rings, layered above the existing static
// cine-bg/cine-grid wash rather than replacing it. Kept off the
// dashboard/incident-detail pages — those already have real-time
// WebSocket traffic to render and don't need a second animation loop
// competing for frames.

interface LayerDef {
  count: number;
  speed: number;
  r: [number, number];
  alpha: number;
  link: number;
}

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  pulse: number;
}

interface Pulse {
  x: number;
  y: number;
  r: number;
  max: number;
  alpha: number;
}

const LAYER_DEFS: LayerDef[] = [
  { count: 40, speed: 0.14, r: [0.5, 1.1], alpha: 0.28, link: 90 },
  { count: 45, speed: 0.3, r: [1.0, 1.9], alpha: 0.55, link: 130 },
  { count: 22, speed: 0.52, r: [1.6, 2.8], alpha: 0.9, link: 170 },
];

const BG_RGB: [number, number, number] = [3, 6, 9];
const PULSE_INTERVAL_FRAMES = 130;

interface CineNetworkBackgroundProps {
  // "soft" (landing page) trims the canvas/frost blur down; other pages
  // keep the original heavier blur.
  blur?: "default" | "soft";
}

export function CineNetworkBackground({ blur = "default" }: CineNetworkBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { theme } = useTheme();

  // Scoped intensity boost: cine-bg/cine-grid live in the root layout and
  // are shared by every page, so the brighter wash this animated layer
  // needs to stay visible over is applied only while this component is
  // mounted (landing page), not globally.
  useEffect(() => {
    document.documentElement.classList.add("cine-boost");
    return () => document.documentElement.classList.remove("cine-boost");
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Reference design is dark-only — the canvas paints its own opaque dark
    // fill every frame, which reads as a broken seam over the light theme's
    // white surfaces. Rather than invent a light-mode palette nobody asked
    // for, just clear the canvas and skip the animation while light theme
    // is active; it resumes cleanly if the user switches back to dark.
    if (theme !== "dark") {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let w = 0;
    let h = 0;
    let dpr = 1;
    let layers: { def: LayerDef; nodes: Node[] }[] = [];
    let pulses: Pulse[] = [];
    let pulseTimer = 0;
    let rafId: number | null = null;
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas!.width = w * dpr;
      canvas!.height = h * dpr;
      canvas!.style.width = `${w}px`;
      canvas!.style.height = `${h}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function makeLayers() {
      const scale = Math.max(0.5, Math.min(1.6, (w * h) / (1400 * 800)));
      layers = LAYER_DEFS.map((def) => ({
        def,
        nodes: Array.from({ length: Math.round(def.count * scale) }, () => ({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * def.speed,
          vy: (Math.random() - 0.5) * def.speed,
          r: def.r[0] + Math.random() * (def.r[1] - def.r[0]),
          pulse: Math.random() * Math.PI * 2,
        })),
      }));
    }

    function paintBase() {
      ctx!.fillStyle = `rgb(${BG_RGB[0]}, ${BG_RGB[1]}, ${BG_RGB[2]})`;
      ctx!.fillRect(0, 0, w, h);
    }

    function spawnPulse() {
      pulses.push({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 0,
        max: 160 + Math.random() * 140,
        alpha: 0.5,
      });
    }

    function draw() {
      pulseTimer++;
      if (pulseTimer > PULSE_INTERVAL_FRAMES) {
        spawnPulse();
        pulseTimer = 0;
      }

      // Trailing fade instead of a hard clear, so nodes leave faint motion trails.
      ctx!.fillStyle = `rgba(${BG_RGB[0]}, ${BG_RGB[1]}, ${BG_RGB[2]}, 0.16)`;
      ctx!.fillRect(0, 0, w, h);

      pulses.forEach((p) => {
        p.r += 1.6;
        p.alpha = Math.max(0, 0.5 * (1 - p.r / p.max));
        ctx!.beginPath();
        ctx!.strokeStyle = `rgba(54, 215, 232, ${p.alpha})`;
        ctx!.lineWidth = 1.2;
        ctx!.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx!.stroke();
      });
      pulses = pulses.filter((p) => p.r < p.max);

      layers.forEach((layer) => {
        const { nodes, def } = layer;
        for (let i = 0; i < nodes.length; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < nodes.length; j++) {
            const b = nodes[j];
            const dx = a.x - b.x;
            const dy = a.y - b.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < def.link) {
              const alpha = (1 - dist / def.link) * def.alpha * 0.6;
              ctx!.strokeStyle = `rgba(54, 215, 232, ${alpha})`;
              ctx!.lineWidth = 1;
              ctx!.beginPath();
              ctx!.moveTo(a.x, a.y);
              ctx!.lineTo(b.x, b.y);
              ctx!.stroke();
            }
          }
        }
        for (const n of nodes) {
          n.pulse += 0.06;
          const glow = def.alpha * (0.6 + Math.sin(n.pulse) * 0.4);
          ctx!.beginPath();
          ctx!.fillStyle = `rgba(160, 245, 255, ${glow})`;
          ctx!.shadowColor = "rgba(54, 215, 232, 0.95)";
          ctx!.shadowBlur = 4 + n.r * 3;
          ctx!.arc(n.x, n.y, n.r, 0, Math.PI * 2);
          ctx!.fill();
          ctx!.shadowBlur = 0;

          n.x += n.vx;
          n.y += n.vy;
          if (n.x < -20) n.x = w + 20;
          if (n.x > w + 20) n.x = -20;
          if (n.y < -20) n.y = h + 20;
          if (n.y > h + 20) n.y = -20;
        }
      });

      if (!reduceMotion) rafId = requestAnimationFrame(draw);
    }

    resize();
    makeLayers();
    paintBase();
    draw();

    const onResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (rafId !== null) cancelAnimationFrame(rafId);
        resize();
        makeLayers();
        paintBase();
        draw();
      }, 200);
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      clearTimeout(resizeTimer);
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [theme]);

  const soft = blur === "soft";

  return (
    <>
      <canvas ref={canvasRef} className={soft ? "bg-canvas bg-canvas--soft" : "bg-canvas"} aria-hidden />
      <div className={soft ? "bg-frost bg-frost--soft" : "bg-frost"} aria-hidden />
    </>
  );
}

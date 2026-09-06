import { useEffect, useRef, useState, useCallback } from "react";

/**
 * Scroll-linked animation hook — returns a ref and a progress value (0..1)
 * that tracks how far the element has scrolled through the viewport.
 *
 * progress = 0 when element top is at viewport bottom
 * progress = 1 when element bottom is at viewport top
 *
 * Designed for premium scroll choreography:
 * - Sticky/pinned sections: parent is tall, child is sticky, progress drives child animation
 * - Parallax: progress drives translateY at different rate
 * - Scale/opacity/translate combos: progress maps to CSS variable or style
 *
 * Uses requestAnimationFrame + passive scroll listener for smooth 60fps updates.
 * Respects prefers-reduced-motion by snapping progress to 0 or 1.
 */
export function useScrollProgress<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [progress, setProgress] = useState(0);
  const rafRef = useRef<number | undefined>(undefined);
  const lastProgress = useRef(0);

  const update = useCallback(() => {
    const el = ref.current;
    if (!el) return;

    const rect = el.getBoundingClientRect();
    const viewportH = window.innerHeight;

    // Element's journey through viewport:
    // Start: top of element at bottom of viewport (rect.top = viewportH)
    // End: bottom of element at top of viewport (rect.bottom = 0)
    const total = rect.height + viewportH;
    const current = viewportH - rect.top;
    const p = Math.max(0, Math.min(1, current / total));

    // Only update if changed meaningfully (avoid re-renders on sub-pixel)
    if (Math.abs(p - lastProgress.current) > 0.001) {
      lastProgress.current = p;
      setProgress(p);
    }
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      // For reduced motion, snap to 1 when element is in viewport at all
      const observer = new IntersectionObserver(
        ([entry]) => {
          setProgress(entry.isIntersecting ? 1 : 0);
        },
        { threshold: 0.1 }
      );
      observer.observe(el);
      return () => observer.disconnect();
    }

    const onScroll = () => {
      if (rafRef.current) return;
      rafRef.current = requestAnimationFrame(() => {
        update();
        rafRef.current = undefined;
      });
    };

    update(); // Initial
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });

    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [update]);

  return { ref, progress };
}

/**
 * Maps a progress value (0..1) through a segment (start..end) to 0..1
 * Example: segmentProgress(0.5, 0.2, 0.6) = (0.5 - 0.2) / (0.6 - 0.2) = 0.75
 */
export function segmentProgress(p: number, start: number, end: number): number {
  return Math.max(0, Math.min(1, (p - start) / (end - start)));
}

/**
 * Easing functions for scroll-linked animations
 */
export const ease = {
  linear: (t: number) => t,
  easeOut: (t: number) => 1 - Math.pow(1 - t, 3),
  easeIn: (t: number) => t * t * t,
  easeInOut: (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  easeOutQuint: (t: number) => 1 - Math.pow(1 - t, 5),
  easeInOutQuint: (t: number) =>
    t < 0.5 ? 16 * t * t * t * t * t : 1 - Math.pow(-2 * t + 2, 5) / 2,
};

/**
 * Converts a 0..1 progress through easing to a CSS transform string pieces
 */
export function mapRange(
  p: number,
  from: [number, number],
  to: [number, number]
): number {
  return from[0] + (from[1] - from[0]) * p * (to[1] - to[0]);
}

/**
 * Interpolate between two numbers based on progress
 */
export function lerp(from: number, to: number, p: number): number {
  return from + (to - from) * p;
}

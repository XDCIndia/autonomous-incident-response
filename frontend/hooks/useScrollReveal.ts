import { useEffect, useRef, useState } from "react";

/**
 * Reveals an element (fade + rise) the first time it scrolls into view, or
 * immediately if it's already on-screen when it mounts — which is what
 * makes this work equally well for a static list revealed by scrolling and
 * a panel that appears dynamically because live data just arrived (e.g. a
 * new pipeline stage's result panel on the incident page). Fires once,
 * then stops watching — not a repeating effect.
 */
export function useScrollReveal<T extends HTMLElement>(delay = 0) {
  const ref = useRef<T>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.unobserve(el);
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -40px 0px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return {
    ref,
    className: `scroll-reveal${visible ? " is-visible" : ""}`,
    style: { transitionDelay: `${delay}ms` },
  };
}

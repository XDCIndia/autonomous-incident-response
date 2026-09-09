// Small display-formatting helpers shared by the console pages.

/** "payment-service" -> "Payment Service" (title-cases hyphen/space-separated
 * service keys for display). Deliberately duplicated from the dashboard
 * page's local copy — that one stays page-local to keep this refactor
 * minimal; new console pages import from here. */
export function serviceDisplayName(key: string): string {
  if (!key) return key;
  return key
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** ISO timestamp -> compact relative label ("just now", "5m ago", "3h ago",
 * "2d ago"); falls back to a short absolute time for anything older. */
export function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const deltaMs = Date.now() - then;
  const sec = Math.round(deltaMs / 1000);
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}

/** ISO timestamp -> short absolute clock label for the current locale. */
export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtMs(ms: number): string {
  if (ms < 1) return "0ms";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function fmtRelative(iso: string): string {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 5) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
    timeZone: "America/Los_Angeles",
  }).format(d);
}

// Parse the nested input_text JSON blob the SDK stores on traces.
// Returns the human-readable task string, or empty string if absent.
export function parseInputText(raw: string | undefined): string {
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw);
    return (parsed.input_text as string) || "";
  } catch {
    return raw;
  }
}

// Stable color per agent name. Picks from our palette so it stays on-brand.
const AGENT_PALETTE = [
  "#C73E1D", // cherry
  "#D9933A", // amber
  "#7A8B5E", // sage
  "#6B7B8C", // slate
  "#A8625C", // muted brick
  "#9C8B6B", // muted gold
  "#5C7060", // deep sage
  "#8A5C7A", // muted plum
];

export function agentColor(agent: string): string {
  let h = 0;
  for (let i = 0; i < agent.length; i++) {
    h = (h * 31 + agent.charCodeAt(i)) | 0;
  }
  return AGENT_PALETTE[Math.abs(h) % AGENT_PALETTE.length];
}

// Map blame score (0-100) to a palette tier.
export function blameTone(score: number): { color: string; label: string } {
  if (score >= 50) return { color: "#C73E1D", label: "high" };
  if (score >= 25) return { color: "#D9933A", label: "elevated" };
  if (score >= 10) return { color: "#7A8B5E", label: "moderate" };
  return { color: "#6B7B8C", label: "low" };
}

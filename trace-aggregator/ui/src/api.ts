import type {
  TraceSummary,
  TraceDetail,
  RawSpan,
  GlobalBlameRow,
  DecisionEvent,
  RootCauseEdge,
} from "./types";

// Vite proxies /api/* to the FastAPI server in dev (see vite.config.ts).
const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${path}`);
  }
  return res.json();
}

export const api = {
  health: () => get<{ ok: boolean }>("/health"),
  listTraces: (limit = 50, hasErrors?: boolean) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (hasErrors !== undefined) params.set("has_errors", String(hasErrors));
    return get<TraceSummary[]>(`/traces?${params}`);
  },
  getTrace: (id: string) => get<TraceDetail>(`/traces/${id}`),
  getRawSpans: (id: string) => get<RawSpan[]>(`/traces/${id}/spans`),
  getDecisions: (id: string) =>
    get<DecisionEvent[]>(`/traces/${id}/decisions`),
  getRootCause: (id: string) =>
    get<RootCauseEdge[]>(`/traces/${id}/root-cause`),
  globalBlame: (hours = 24) =>
    get<GlobalBlameRow[]>(`/agents/blame?hours=${hours}`),
};

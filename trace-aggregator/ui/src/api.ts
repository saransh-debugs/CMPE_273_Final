import type {
  TraceSummary,
  TraceListResponse,
  TraceDetail,
  GlobalBlameRow,
  DecisionEvent,
  RootCauseEdge,
  SLOResponse,
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
  listTraces: (
  limit = 50,
  hasErrors?: boolean,
  cursor?: string,
  ): Promise<TraceListResponse> => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (hasErrors !== undefined) params.set("has_errors", String(hasErrors));
    if (cursor) params.set("cursor", cursor);
    return get<TraceListResponse>(`/traces?${params}`);
  },
  getTrace: (id: string) => get<TraceDetail>(`/traces/${id}`),
  getDecisions: (id: string) =>
    get<DecisionEvent[]>(`/traces/${id}/decisions`),
  getRootCause: (id: string) =>
    get<RootCauseEdge[]>(`/traces/${id}/root-cause`),
  globalBlame: (hours = 24) =>
    get<GlobalBlameRow[]>(`/agents/blame?hours=${hours}`),
  slo: (historyLimit = 20) => get<SLOResponse>(`/slo?history_limit=${historyLimit}`),
};

/**
 * Open an SSE connection to /traces/stream.
 *
 * Returns a cleanup function that closes the connection. Typical usage:
 *
 *   useEffect(() => {
 *     if (!live) return;
 *     const close = openTraceStream({
 *       onTrace: (t) => setTraces(prev => mergeNew(prev, t)),
 *       onError: (err) => setError(err),
 *     });
 *     return close;
 *   }, [live]);
 *
 * EventSource auto-reconnects on transient network failures, so we don't
 * need our own retry logic.
 */
export function openTraceStream(opts: {
  hasErrors?: boolean;
  agentId?: string;
  onTrace: (t: TraceSummary) => void;
  onHeartbeat?: (ts: number) => void;
  onError?: (msg: string) => void;
}): () => void {
  const params = new URLSearchParams();
  if (opts.hasErrors !== undefined) params.set("has_errors", String(opts.hasErrors));
  if (opts.agentId) params.set("agent_id", opts.agentId);

  const url = `${BASE}/traces/stream${params.toString() ? "?" + params : ""}`;
  const es = new EventSource(url);

  es.addEventListener("trace_update", (e: MessageEvent) => {
    try {
      const trace = JSON.parse(e.data) as TraceSummary;
      opts.onTrace(trace);
    } catch (err) {
      opts.onError?.(`Parse error: ${err}`);
    }
  });

  es.addEventListener("heartbeat", (e: MessageEvent) => {
    try {
      const { ts } = JSON.parse(e.data);
      opts.onHeartbeat?.(ts);
    } catch {
      /* ignore */
    }
  });

  es.addEventListener("error", (e) => {
    // The browser fires "error" for both server-sent error events AND
    // network drops. Either way the EventSource will try to reconnect
    // automatically — we surface it only as a non-fatal warning.
    opts.onError?.("Connection interrupted (reconnecting…)");
  });

  // Caller invokes this to tear down.
  return () => es.close();
}

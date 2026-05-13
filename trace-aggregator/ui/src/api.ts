import type {
  TraceSummary,
  TraceListResponse,
  TraceDetail,
  GlobalBlameResponse,
  DecisionEvent,
  RootCauseEdge,
  RawSpan,
  SLOResponse,
  Incident,
  IncidentListResponse,
  IncidentState,
} from "./types";

// In dev, this defaults to /api (Vite proxy). In Cloud Run, set VITE_API_BASE_URL.
const BASE = import.meta.env.VITE_API_BASE_URL || "/api";

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
  agentId?: string,
  ): Promise<TraceListResponse> => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (hasErrors !== undefined) params.set("has_errors", String(hasErrors));
    if (cursor) params.set("cursor", cursor);
    if (agentId) params.set("agent_id", agentId);
    return get<TraceListResponse>(`/traces?${params}`);
  },
  getTrace: (id: string) => get<TraceDetail>(`/traces/${id}`),
  getSpans: (id: string) => get<RawSpan[]>(`/traces/${id}/spans`),
  getDecisions: (id: string) =>
    get<DecisionEvent[]>(`/traces/${id}/decisions`),
  getRootCause: (id: string) =>
    get<RootCauseEdge[]>(`/traces/${id}/root-cause`),
  globalBlame: (hours = 24, modelVersion: "v1" | "v2" = "v1") =>
    get<GlobalBlameResponse>(`/agents/blame?hours=${hours}&model_version=${modelVersion}`),
  slo: (historyLimit = 20) => get<SLOResponse>(`/slo?history_limit=${historyLimit}`),
  listIncidents: (state?: IncidentState) => {
    const qs = state ? `?state=${state}` : "";
    return get<IncidentListResponse>(`/incidents${qs}`);
  },
  ackIncident: (key: string): Promise<Incident> =>
    fetch(`${BASE}/incidents/${encodeURIComponent(key)}/ack`, { method: "POST" })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      }),
  resolveIncident: (key: string): Promise<Incident> =>
    fetch(`${BASE}/incidents/${encodeURIComponent(key)}/resolve`, { method: "POST" })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      }),
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

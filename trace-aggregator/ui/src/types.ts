// Mirrors the FastAPI schema from api/main.py.
// If the API changes, update both ends together.

export interface TraceSummary {
  trace_id: string;
  span_count: number;
  total_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  error_count: number;
  reconstructed_at: string;
  input_text?: string;
}

export interface DAGNode {
  span_id: string;
  parent_span_id?: string | null;
  inferred_parent: boolean;
  children: string[];
  agent_id: string;
  event_type: string;
  vector_clock: Record<string, number>;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  start_time_ms: number;
  metadata?: string;
}

export interface AgentBlame {
  agent_id: string;
  span_count: number;
  total_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  error_count: number;
  latency_share_pct: number;
  token_share_pct: number;
  blame_score: number;
}

export interface TraceDetail {
  trace_id: string;
  span_count: number;
  total_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  error_count: number;
  dag: { nodes: DAGNode[]; inferred_parents: string[] };
  blame: AgentBlame[];
  decisions: DecisionEvent[];
  decision_count: number;
  reconstructed_at: string;
  input_text?: string;
}

export interface RawSpan {
  span_id: string;
  parent_span_id?: string | null;
  agent_id: string;
  vector_clock: Record<string, number>;
  event_type: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  start_time_ms: number;
  metadata: string;
}

export interface GlobalBlameRow {
  agent_id: string;
  trace_count: number;
  avg_blame_score: number;
  total_latency_ms: number;
  total_input_tokens: number;
  total_output_tokens: number;
  error_count: number;
  // V2-only fields (present when model_version=v2)
  avg_blame_score_ci_low?: number;
  avg_blame_score_ci_high?: number;
  avg_blame_score_std?: number;
  avg_error_amplification?: number;
  model_version?: string;
}

export interface GlobalBlameResponse {
  hours: number;
  model_version: "v1" | "v2";
  agents: GlobalBlameRow[];
}

export interface DecisionCandidate {
  candidate_id: string;
  description?: string;
  candidate_type?: string;
  score: number;
  reason?: string;
  pros?: string[];
  cons?: string[];
}

export interface DecisionEvent {
  trace_id: string;
  decision_id: string;
  source_span_id: string;
  actor_agent_id: string;
  decision_type: string;
  selected_candidate_id: string;
  confidence: number;
  rationale_summary: string;
  evidence_refs: string[];
  candidates: DecisionCandidate[];
  timestamp_ms: number;
  metadata: string;
}

export interface SLOStatus {
  name: string;
  title: string;
  signal: string;
  window_minutes: number;
  threshold: number;
  comparison: string;
  unit: string;
  value: number;
  passing: boolean;
  sample_count: number;
  notes: string;
}

export interface SLOHistoryPoint {
  evaluated_at: string;
  value: number;
  passing: boolean;
  sample_count: number;
  notes: string;
}

export interface SLOResponse {
  overall: "pass" | "fail" | "unknown";
  statuses: SLOStatus[];
  history: Record<string, SLOHistoryPoint[]>;
}

export interface RootCauseEdge {
  decision_id: string;
  source_span_id: string;
  target_span_id: string;
  decision_type: string;
  actor_agent_id: string;
  selected_candidate_id: string;
  confidence: number;
  rationale_summary: string;
  impact_latency_ms: number;
  impact_tokens: number;
  impact_error_count: number;
  impact_score: number;
  uncertainty: string;
  chain_rank?: number;
}

export type TraceListResponse = {
  items: TraceSummary[];
  next_cursor: string | null;
  has_more: boolean;
  limit: number;
};

export type IncidentState = "open" | "ack" | "resolved";

export interface Incident {
  incident_key: string;
  alert_type: string;
  state: IncidentState;
  severity: "high" | "medium" | "low";
  message: string;
  details: Record<string, unknown>;
  opened_at: string;
  last_seen_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  occurrence_count: number;
}

export interface IncidentListResponse {
  items: Incident[];
  counts: { open: number; ack: number; resolved: number };
}
import type { DecisionEvent, RootCauseEdge } from "../types";
import { fmtMs } from "../utils/format";

interface Props {
  decisions: DecisionEvent[];
  rootCause: RootCauseEdge[];
  selectedDecisionId?: string | null;
  onSelectDecision?: (decisionId: string | null) => void;
}

function parseReasoning(d: DecisionEvent): string {
  try {
    const meta = JSON.parse(d.metadata || "{}");
    return typeof meta.reasoning === "string" ? meta.reasoning : "";
  } catch {
    return "";
  }
}

function impactByDecision(edges: RootCauseEdge[]) {
  const byDecision = new Map<
    string,
    { latency: number; tokens: number; errors: number; targets: Set<string> }
  >();
  for (const edge of edges) {
    const cur =
      byDecision.get(edge.decision_id) || { latency: 0, tokens: 0, errors: 0, targets: new Set<string>() };
    if (!cur.targets.has(edge.target_span_id)) {
      cur.latency += edge.impact_latency_ms;
      cur.tokens += edge.impact_tokens;
      cur.errors += edge.impact_error_count;
    }
    cur.targets.add(edge.target_span_id);
    byDecision.set(edge.decision_id, cur);
  }
  return byDecision;
}

export function DecisionChain({
  decisions,
  rootCause,
  selectedDecisionId,
  onSelectDecision,
}: Props) {
  if (!decisions.length) {
    return <div className="font-mono text-[12px] text-cream-500">No decision events captured for this trace.</div>;
  }
  const impact = impactByDecision(rootCause);

  return (
    <div className="space-y-3">
      {decisions.map((d) => {
        const i = impact.get(d.decision_id);
        const selected = selectedDecisionId === d.decision_id;
        return (
          <button
            key={d.decision_id}
            type="button"
            onClick={() => onSelectDecision?.(selected ? null : d.decision_id)}
            className={`w-full text-left hairline rounded-sm p-4 bg-ink-700/20 transition-colors ${
              selected ? "border-cream-300/50 bg-ink-700/45" : "hover:bg-ink-700/35"
            }`}
          >
            <div className="flex justify-between items-center mb-2">
              <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-cream-400">
                {d.actor_agent_id} {"->"} {d.selected_candidate_id || "unknown"}
              </div>
              <div className="font-mono text-[10px] text-cream-500">
                {d.decision_type} | conf {Math.round(d.confidence * 100)}%
              </div>
            </div>
            <div className="font-display italic text-[17px] text-cream-100 mb-2">{d.rationale_summary || "No rationale provided."}</div>
            {parseReasoning(d) && (
              <div className="mb-2 pl-3 border-l-2 border-cream-700/40 font-mono text-[11px] leading-relaxed text-cream-400 italic">
                <span className="text-cream-500 not-italic">{d.actor_agent_id}:</span>{" "}
                &ldquo;{parseReasoning(d)}&rdquo;
              </div>
            )}
            <div className="font-mono text-[11px] text-cream-500">
              evidence: {d.evidence_refs.length ? d.evidence_refs.join(", ") : "none"}
            </div>
            <div className="mt-2 font-mono text-[11px] text-cream-400">
              impact: {i ? `${fmtMs(i.latency)} | ${i.tokens}t | ${i.errors} errors | ${i.targets.size} targets` : "pending reconstruction"}
            </div>
            {rootCause.find((r) => r.decision_id === d.decision_id) && (
              <div className="mt-1 font-mono text-[10px] text-cream-500">
                score {rootCause.find((r) => r.decision_id === d.decision_id)?.impact_score.toFixed(2)} ·
                uncertainty {rootCause.find((r) => r.decision_id === d.decision_id)?.uncertainty}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

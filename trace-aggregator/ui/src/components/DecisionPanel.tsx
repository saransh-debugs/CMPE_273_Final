import { useState } from "react";
import type { DecisionEvent, RootCauseEdge } from "../types";
import { agentColor, fmtMs, fmtTokens, shortId } from "../utils/format";

interface Props {
  decisions: DecisionEvent[];
  rootCause: RootCauseEdge[];
}

export function DecisionPanel({ decisions, rootCause }: Props) {
  const [tab, setTab] = useState<"decisions" | "root-cause">("decisions");

  if (decisions.length === 0 && rootCause.length === 0) {
    return (
      <div className="text-cream-500 italic font-display text-[14px]">
        No decision events recorded for this trace.
      </div>
    );
  }

  return (
    <div>
      {/* Tab switcher */}
      <div className="flex gap-1 hairline rounded-sm overflow-hidden mb-5 w-fit">
        {(["decisions", "root-cause"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
              tab === t
                ? "bg-cream-50 text-ink-900"
                : "text-cream-300 hover:bg-ink-700"
            }`}
          >
            {t === "decisions"
              ? `decisions (${decisions.length})`
              : `root-cause (${rootCause.length})`}
          </button>
        ))}
      </div>

      {tab === "decisions" && (
        <div className="space-y-3">
          {decisions.map((d) => (
            <DecisionCard key={d.decision_id} d={d} />
          ))}
        </div>
      )}

      {tab === "root-cause" && (
        <div className="space-y-3">
          {rootCause.length === 0 ? (
            <div className="text-cream-500 italic font-display text-[14px]">
              No impact edges computed yet.
            </div>
          ) : (
            rootCause.map((e, i) => <RootCauseCard key={i} edge={e} rank={i + 1} />)
          )}
        </div>
      )}
    </div>
  );
}

function DecisionCard({ d }: { d: DecisionEvent }) {
  const [open, setOpen] = useState(false);
  const c = agentColor(d.actor_agent_id);
  const confidencePct = Math.round(d.confidence * 100);

  return (
    <div className="hairline rounded-sm bg-ink-700/40 overflow-hidden">
      {/* Header row */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-4 py-3 hover:bg-ink-700/70 transition-colors"
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span
              className="inline-block w-2 h-2 rounded-full shrink-0"
              style={{ background: c }}
            />
            <span className="font-mono text-[13px] text-cream-50">
              {d.actor_agent_id}
            </span>
            <span className="font-mono text-[10px] uppercase tracking-wider text-cream-500 hairline px-2 py-0.5 rounded-full">
              {d.decision_type}
            </span>
          </div>
          <div className="flex items-center gap-4 shrink-0">
            <ConfidencePip pct={confidencePct} />
            <span className="font-mono text-[10px] text-cream-500">
              {open ? "▲" : "▼"}
            </span>
          </div>
        </div>

        {/* Rationale preview */}
        {d.rationale_summary && (
          <div className="mt-1.5 font-display italic text-[13px] text-cream-300 leading-snug line-clamp-1">
            "{d.rationale_summary}"
          </div>
        )}
      </button>

      {/* Expanded detail */}
      {open && (
        <div className="px-4 pb-4 border-t border-ink-500/40 pt-4 space-y-4">
          {/* Rationale full */}
          {d.rationale_summary && (
            <div>
              <div className="eyebrow mb-1">rationale</div>
              <div className="font-display italic text-[14px] text-cream-100 leading-relaxed">
                "{d.rationale_summary}"
              </div>
            </div>
          )}

          {/* Candidates */}
          {d.candidates.length > 0 && (
            <div>
              <div className="eyebrow mb-2">candidates</div>
              <div className="space-y-2">
                {d.candidates.map((cand) => {
                  const isSelected = cand.candidate_id === d.selected_candidate_id;
                  return (
                    <div
                      key={cand.candidate_id}
                      className={`hairline rounded-sm px-3 py-2 ${
                        isSelected
                          ? "border-cherry/50 bg-cherry/5"
                          : "bg-ink-600/40"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-mono text-[12px] text-cream-50">
                          {cand.description || cand.reason || cand.candidate_id}
                        </span>
                        <div className="flex items-center gap-2">
                          {isSelected && (
                            <span className="font-mono text-[9px] uppercase tracking-wider text-cherry-light">
                              selected
                            </span>
                          )}
                          <span className="font-mono text-[11px] text-cream-300 tabular-nums">
                            {(cand.score * 100).toFixed(0)}%
                          </span>
                        </div>
                      </div>
                      {((cand.pros?.length ?? 0) > 0 || (cand.cons?.length ?? 0) > 0) && (
                        <div className="grid grid-cols-2 gap-3 mt-1.5">
                          {(cand.pros?.length ?? 0) > 0 && (
                            <div>
                              <div className="font-mono text-[9px] uppercase tracking-wider text-sage mb-0.5">
                                pros
                              </div>
                              {cand.pros!.map((p, i) => (
                                <div key={i} className="font-mono text-[10px] text-cream-300">
                                  + {p}
                                </div>
                              ))}
                            </div>
                          )}
                          {(cand.cons?.length ?? 0) > 0 && (
                            <div>
                              <div className="font-mono text-[9px] uppercase tracking-wider text-cherry mb-0.5">
                                cons
                              </div>
                              {cand.cons!.map((c, i) => (
                                <div key={i} className="font-mono text-[10px] text-cream-300">
                                  − {c}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Evidence */}
          {d.evidence_refs.length > 0 && (
            <div>
              <div className="eyebrow mb-1">evidence</div>
              <div className="flex flex-wrap gap-1.5">
                {d.evidence_refs.map((ref, i) => (
                  <span key={i} className="tag">{ref}</span>
                ))}
              </div>
            </div>
          )}

          {/* Footer meta */}
          <div className="flex gap-6 font-mono text-[10px] text-cream-500 pt-1 border-t border-ink-500/30">
            <span>span {shortId(d.source_span_id)}</span>
            <span>decision {shortId(d.decision_id)}</span>
            <span>confidence {confidencePct}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

function RootCauseCard({ edge, rank }: { edge: RootCauseEdge; rank: number }) {
  const c = agentColor(edge.actor_agent_id);
  const hasErrors = edge.impact_error_count > 0;

  return (
    <div className="hairline rounded-sm bg-ink-700/40 p-4">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-cream-500 tabular-nums w-5">
            {String(rank).padStart(2, "0")}
          </span>
          <span
            className="inline-block w-2 h-2 rounded-full shrink-0"
            style={{ background: c }}
          />
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-[13px] text-cream-50">
                {edge.actor_agent_id}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-wider text-cream-500 hairline px-2 py-0.5 rounded-full">
                {edge.decision_type}
              </span>
              {hasErrors && (
                <span className="font-mono text-[10px] uppercase tracking-wider text-cherry">
                  {edge.impact_error_count} error{edge.impact_error_count > 1 ? "s" : ""}
                </span>
              )}
            </div>
            {edge.rationale_summary && (
              <div className="mt-0.5 font-display italic text-[12px] text-cream-400">
                "{edge.rationale_summary}"
              </div>
            )}
          </div>
        </div>
        <ConfidencePip pct={Math.round(edge.confidence * 100)} />
      </div>

      {/* Impact metrics */}
      <div className="grid grid-cols-3 gap-3">
        <ImpactStat
          label="latency impact"
          value={fmtMs(edge.impact_latency_ms)}
          hot={edge.impact_latency_ms > 500}
        />
        <ImpactStat
          label="token impact"
          value={fmtTokens(edge.impact_tokens)}
          hot={edge.impact_tokens > 1000}
        />
        <ImpactStat
          label="error impact"
          value={String(edge.impact_error_count)}
          hot={edge.impact_error_count > 0}
        />
      </div>

      <div className="mt-3 flex gap-6 font-mono text-[10px] text-cream-500 border-t border-ink-500/30 pt-2">
        <span>src {shortId(edge.source_span_id)}</span>
        <span>→ {shortId(edge.target_span_id)}</span>
        <span>decision {shortId(edge.decision_id)}</span>
      </div>
    </div>
  );
}

function ConfidencePip({ pct }: { pct: number }) {
  const color =
    pct >= 80 ? "#7A8B5E" : pct >= 50 ? "#D9933A" : "#C73E1D";
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <div className="w-12 h-1 bg-ink-500/40 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="font-mono text-[10px] tabular-nums" style={{ color }}>
        {pct}%
      </span>
    </div>
  );
}

function ImpactStat({
  label,
  value,
  hot,
}: {
  label: string;
  value: string;
  hot?: boolean;
}) {
  return (
    <div>
      <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-cream-500 mb-0.5">
        {label}
      </div>
      <div
        className={`font-mono text-[13px] tabular-nums ${
          hot ? "text-cherry-light" : "text-cream-50"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

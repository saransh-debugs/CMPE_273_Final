import type { AgentBlame } from "../types";
import { agentColor, fmtMs, fmtTokens, blameTone } from "../utils/format";

interface Props {
  rows: AgentBlame[];
}

export function BlamePanel({ rows }: Props) {
  const sorted = [...rows].sort((a, b) => b.blame_score - a.blame_score);
  if (sorted.length === 0) {
    return (
      <div className="text-cream-500 italic font-display">No blame data yet.</div>
    );
  }
  const maxScore = Math.max(...sorted.map((r) => r.blame_score), 1);

  return (
    <div className="space-y-3">
      {sorted.map((r, i) => {
        const tone = blameTone(r.blame_score);
        const barWidth = (r.blame_score / maxScore) * 100;
        const c = agentColor(r.agent_id);

        return (
          <div
            key={r.agent_id}
            className="hairline rounded-sm bg-ink-700/40 p-4 hover:bg-ink-700/70 transition-colors"
          >
            <div className="flex items-baseline justify-between mb-3">
              <div className="flex items-baseline gap-3">
                <span className="font-mono text-[10px] text-cream-500 tabular-nums">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span
                  className="inline-block w-2 h-2 rounded-full"
                  style={{ background: c }}
                />
                <span className="font-mono text-cream-50 text-[14px]">
                  {r.agent_id}
                </span>
                {r.error_count > 0 && (
                  <span className="font-mono text-[10px] uppercase tracking-tighter text-cherry">
                    {r.error_count} error{r.error_count > 1 ? "s" : ""}
                  </span>
                )}
              </div>
              <div className="flex items-baseline gap-2">
                <span
                  className="font-display text-[28px] leading-none tracking-tighter tabular-nums"
                  style={{ color: tone.color }}
                >
                  {r.blame_score.toFixed(1)}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-cream-500">
                  {tone.label}
                </span>
              </div>
            </div>

            {/* Composite bar */}
            <div className="h-1.5 bg-ink-500/40 rounded-full overflow-hidden mb-3">
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{ width: `${barWidth}%`, background: tone.color }}
              />
            </div>

            {/* Breakdown grid */}
            <div className="grid grid-cols-4 gap-3 text-[11px]">
              <Stat label="latency" value={fmtMs(r.total_latency_ms)} share={r.latency_share_pct} />
              <Stat
                label="tokens"
                value={fmtTokens(r.total_input_tokens + r.total_output_tokens)}
                share={r.token_share_pct}
              />
              <Stat label="spans" value={String(r.span_count)} />
              <Stat label="errors" value={String(r.error_count)} alert={r.error_count > 0} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Stat({
  label,
  value,
  share,
  alert,
}: {
  label: string;
  value: string;
  share?: number;
  alert?: boolean;
}) {
  return (
    <div>
      <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-cream-500 mb-1">
        {label}
      </div>
      <div
        className={`font-mono text-[13px] tabular-nums ${
          alert ? "text-cherry-light" : "text-cream-50"
        }`}
      >
        {value}
        {share !== undefined && (
          <span className="text-cream-500 text-[10px] ml-1.5">
            {share.toFixed(0)}%
          </span>
        )}
      </div>
    </div>
  );
}

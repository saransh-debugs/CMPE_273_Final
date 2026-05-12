import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { api } from "../api";
import type { GlobalBlameRow } from "../types";
import { fmtMs, fmtTokens, agentColor } from "../utils/format";

type Model = "v1" | "v2";

export function BlamePage() {
  const [rows, setRows] = useState<GlobalBlameRow[]>([]);
  const [hours, setHours] = useState(24);
  const [model, setModel] = useState<Model>("v1");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .globalBlame(hours, model)
      .then((res) => { setRows(res.agents); setLoading(false); })
      .catch((e) => { setError(String(e)); setLoading(false); });
  }, [hours, model]);

  const maxLatency = rows.reduce(
    (max, row) => Math.max(max, row.total_latency_ms),
    1
  );

  return (
    <div className="max-w-[1100px] mx-auto px-8 py-12">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="mb-12"
      >
        <div className="eyebrow mb-3">cross-trace attribution</div>
        <h1 className="font-display text-[64px] leading-[0.95] tracking-tightest text-cream-50">
          The <span className="italic text-cherry-light">blame</span> ledger
        </h1>
        <p className="mt-4 font-display italic text-[18px] text-cream-300 max-w-xl">
          Which agents are burning time, tokens, and trust across every recent run?
          Click an agent to see only its traces.
        </p>
      </motion.div>

      <div className="flex items-center justify-between mb-8 gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          {/* Time window selector */}
          <div className="flex gap-1 hairline rounded-sm overflow-hidden">
            {[1, 6, 24, 168].map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHours(h)}
                className={`px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
                  hours === h
                    ? "bg-cream-50 text-ink-900"
                    : "text-cream-300 hover:bg-ink-700"
                }`}
              >
                {h === 168 ? "7d" : `${h}h`}
              </button>
            ))}
          </div>

          {/* V1 / V2 model toggle */}
          <div className="flex gap-1 hairline rounded-sm overflow-hidden">
            {(["v1", "v2"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setModel(m)}
                className={`px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
                  model === m
                    ? "bg-cream-50 text-ink-900"
                    : "text-cream-300 hover:bg-ink-700"
                }`}
                title={
                  m === "v1"
                    ? "Point estimates only"
                    : "Adds 95% confidence intervals and DAG-depth error amplification"
                }
              >
                {m}
              </button>
            ))}
          </div>
        </div>

        <div className="font-mono text-[11px] text-cream-500">
          {loading ? "loading…" : `${rows.length} agents`}
        </div>
      </div>

      {error && (
        <div className="hairline rounded-sm bg-cherry/10 border-cherry/40 p-4 text-cherry-light font-mono text-[12px] mb-6">
          API error: {error}. Is the FastAPI server running on :8000?
        </div>
      )}

      {!error && !loading && rows.length === 0 && (
        <div className="hairline rounded-sm p-12 text-center">
          <div className="font-display italic text-[20px] text-cream-300 mb-2">
            No agent data yet.
          </div>
          <div className="font-mono text-[12px] text-cream-500">
            Run <span className="text-cream-100">python -m demo.pipeline</span> to
            emit some traces.
          </div>
        </div>
      )}

      <div className="space-y-2">
        {rows.map((r, i) => {
          const widthPct = Math.max(0, (r.total_latency_ms / maxLatency) * 100);
          const c = agentColor(r.agent_id);
          return (
            <motion.div
              key={r.agent_id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.35, delay: Math.min(i * 0.04, 0.5) }}
            >
              <Link
                to={`/?agent_id=${encodeURIComponent(r.agent_id)}`}
                title={`Show traces involving ${r.agent_id}`}
                className="block hairline rounded-sm p-5 bg-ink-700/30 hover:bg-ink-700/60 hover:border-cherry/30 transition-colors group cursor-pointer"
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-baseline gap-3">
                    <span className="font-mono text-[10px] text-cream-500 tabular-nums w-6">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span
                      className="inline-block w-2.5 h-2.5 rounded-full"
                      style={{ background: c }}
                    />
                    <span className="font-display text-[22px] italic tracking-tighter text-cream-50 group-hover:text-cherry-light transition-colors">
                      {r.agent_id}
                    </span>
                    {r.error_count > 0 && (
                      <span className="font-mono text-[10px] uppercase tracking-tighter text-cherry">
                        {r.error_count} error{r.error_count > 1 ? "s" : ""}
                      </span>
                    )}
                  </div>
                  <div className="font-mono text-[11px] text-cream-500">
                    {r.trace_count} trace{r.trace_count > 1 ? "s" : ""}
                  </div>
                </div>

                <div className="grid grid-cols-[1fr_auto] gap-6 items-center">
                  <div className="h-1 bg-ink-500/40 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{ width: `${widthPct}%`, background: c }}
                    />
                  </div>
                  <div className="flex gap-6 font-mono text-[12px] tabular-nums">
                    <Stat
                      label="blame"
                      value={`${r.avg_blame_score.toFixed(1)}`}
                      sub={
                        model === "v2" && r.avg_blame_score_ci_low !== undefined
                          ? `CI ${r.avg_blame_score_ci_low.toFixed(0)}–${r.avg_blame_score_ci_high?.toFixed(0)}`
                          : undefined
                      }
                    />
                    <Stat label="lat" value={fmtMs(r.total_latency_ms)} />
                    <Stat
                      label="tok"
                      value={fmtTokens(r.total_input_tokens + r.total_output_tokens)}
                    />
                  </div>
                </div>

                {model === "v2" && (
                  <div className="mt-3 pt-3 border-t border-ink-500/30 flex gap-6 font-mono text-[10px] text-cream-500">
                    <span>
                      std σ <span className="text-cream-100 tabular-nums">{r.avg_blame_score_std?.toFixed(2) ?? "—"}</span>
                    </span>
                    <span>
                      err amp <span className={`tabular-nums ${(r.avg_error_amplification ?? 0) > 0 ? "text-cherry-light" : "text-cream-100"}`}>
                        ×{r.avg_error_amplification?.toFixed(2) ?? "—"}
                      </span>
                    </span>
                    <span className="text-cream-500/70 italic">{r.model_version ?? "v2"}</span>
                  </div>
                )}
              </Link>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <span className="text-cream-500 text-[10px] uppercase tracking-wider mr-2">
        {label}
      </span>
      <span className="text-cream-50">{value}</span>
      {sub && (
        <span className="ml-1 text-cream-500 text-[10px]">({sub})</span>
      )}
    </div>
  );
}

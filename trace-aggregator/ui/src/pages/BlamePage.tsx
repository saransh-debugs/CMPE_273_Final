import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../api";
import type { GlobalBlameRow } from "../types";
import { fmtMs, fmtTokens, agentColor } from "../utils/format";

export function BlamePage() {
  const [rows, setRows] = useState<GlobalBlameRow[]>([]);
  const [hours, setHours] = useState(24);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .globalBlame(hours)
      .then((data) => { setRows(data); setLoading(false); })
      .catch((e) => { setError(String(e)); setLoading(false); });
  }, [hours]);

  // Keep denominator stable even before data loads (or if all latencies are 0).
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
        </p>
      </motion.div>

      <div className="flex items-center justify-between mb-8">
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
              className="hairline rounded-sm p-5 bg-ink-700/30 hover:bg-ink-700/60 transition-colors group"
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
                  <span className="font-display text-[22px] italic tracking-tighter text-cream-50">
                    {r.agent_id}
                  </span>
                  {r.error_count > 0 && (
                    <span className="font-mono text-[10px] uppercase tracking-tighter text-cherry">
                      {r.error_count} error{r.error_count > 1 ? "s" : ""}
                    </span>
                  )}
                </div>
                <div className="font-mono text-[11px] text-cream-500">
                  {r.spans} span{r.spans > 1 ? "s" : ""}
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
                  <div>
                    <span className="text-cream-500 text-[10px] uppercase tracking-wider mr-2">
                      lat
                    </span>
                    <span className="text-cream-50">{fmtMs(r.total_latency_ms)}</span>
                  </div>
                  <div>
                    <span className="text-cream-500 text-[10px] uppercase tracking-wider mr-2">
                      tok
                    </span>
                    <span className="text-cream-50">
                      {fmtTokens(r.total_input_tokens + r.total_output_tokens)}
                    </span>
                  </div>
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

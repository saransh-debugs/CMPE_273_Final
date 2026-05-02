import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../api";
import type { SLOResponse, SLOStatus } from "../types";
import { fmtRelative } from "../utils/format";

const POLL_MS = 15_000;

function formatValue(s: SLOStatus): string {
  if (s.unit === "ms") return `${s.value.toFixed(0)}ms`;
  if (s.unit === "ratio") return `${(s.value * 100).toFixed(2)}%`;
  return s.value.toFixed(3);
}

function formatThreshold(s: SLOStatus): string {
  if (s.unit === "ms") return `${s.threshold}ms`;
  if (s.unit === "ratio") return `${(s.threshold * 100).toFixed(1)}%`;
  return s.threshold.toFixed(3);
}

export function SLOPage() {
  const [data, setData] = useState<SLOResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      api
        .slo(20)
        .then((d) => {
          if (cancelled) return;
          setData(d);
          setError(null);
          setLoading(false);
        })
        .catch((e) => {
          if (cancelled) return;
          setError(String(e));
          setLoading(false);
        });
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="max-w-[1100px] mx-auto px-8 py-12">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="mb-12"
      >
        <div className="eyebrow mb-3">reliability budget</div>
        <h1 className="font-display text-[64px] leading-[0.95] tracking-tightest text-cream-50">
          The <span className="italic text-cherry-light">SLO</span> ledger
        </h1>
        <p className="mt-4 font-display italic text-[18px] text-cream-300 max-w-xl">
          Promises we make about ingest, reconstruction, and query — measured live.
        </p>
      </motion.div>

      {error && (
        <div className="hairline rounded-sm bg-cherry/10 border-cherry/40 p-4 text-cherry-light font-mono text-[12px] mb-6">
          API error: {error}. Is the FastAPI server running on :8000?
        </div>
      )}

      {!error && loading && (
        <div className="font-mono text-[12px] text-cream-500">loading…</div>
      )}

      {!error && data && (
        <>
          <div
            className={`hairline rounded-sm px-5 py-4 mb-8 flex items-center justify-between ${
              data.overall === "pass"
                ? "bg-sage-900/40"
                : data.overall === "fail"
                ? "bg-cherry/10 border-cherry/40"
                : "bg-ink-700/40"
            }`}
          >
            <div className="flex items-baseline gap-3">
              <span className="eyebrow">overall</span>
              <span
                className={`font-display italic text-[28px] tracking-tighter ${
                  data.overall === "fail" ? "text-cherry-light" : "text-cream-50"
                }`}
              >
                {data.overall.toUpperCase()}
              </span>
            </div>
            <div className="font-mono text-[11px] text-cream-500">
              {data.statuses.length} SLOs evaluated
            </div>
          </div>

          <div className="space-y-3">
            {data.statuses.map((s, i) => {
              const history = data.history[s.name] || [];
              return (
                <motion.div
                  key={s.name}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.3, delay: Math.min(i * 0.04, 0.3) }}
                  className={`hairline rounded-sm p-5 ${
                    s.passing ? "bg-ink-700/30" : "bg-cherry/10 border-cherry/40"
                  }`}
                >
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <div className="font-display text-[22px] italic tracking-tighter text-cream-50">
                        {s.title}
                      </div>
                      <div className="font-mono text-[11px] text-cream-500 mt-1">
                        {s.name} · window={s.window_minutes}m · samples={s.sample_count}
                      </div>
                    </div>
                    <span
                      className={`font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-sm ${
                        s.passing
                          ? "bg-sage-900/60 text-cream-50"
                          : "bg-cherry text-cream-50"
                      }`}
                    >
                      {s.passing ? "pass" : "fail"}
                    </span>
                  </div>

                  <div className="grid grid-cols-3 gap-6 font-mono text-[13px] tabular-nums">
                    <div>
                      <span className="text-cream-500 text-[10px] uppercase tracking-wider mr-2">
                        value
                      </span>
                      <span
                        className={
                          s.passing ? "text-cream-50" : "text-cherry-light"
                        }
                      >
                        {formatValue(s)}
                      </span>
                    </div>
                    <div>
                      <span className="text-cream-500 text-[10px] uppercase tracking-wider mr-2">
                        target
                      </span>
                      <span className="text-cream-300">
                        {s.comparison} {formatThreshold(s)}
                      </span>
                    </div>
                    <div className="text-right">
                      {history.length > 0 && (
                        <span className="text-cream-500 text-[11px]">
                          last {history.length} eval{history.length === 1 ? "" : "s"}
                          {" · "}
                          {fmtRelative(history[0].evaluated_at)}
                        </span>
                      )}
                    </div>
                  </div>

                  {s.notes && (
                    <div className="mt-3 font-mono text-[11px] text-cream-500 italic">
                      {s.notes}
                    </div>
                  )}

                  {history.length > 1 && (
                    <div className="mt-4 flex gap-0.5 h-6">
                      {history
                        .slice()
                        .reverse()
                        .map((h, idx) => (
                          <div
                            key={idx}
                            title={`${formatValue({ ...s, value: h.value })} @ ${h.evaluated_at}`}
                            className={`flex-1 rounded-sm ${
                              h.passing ? "bg-sage" : "bg-cherry"
                            } opacity-80`}
                          />
                        ))}
                    </div>
                  )}
                </motion.div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

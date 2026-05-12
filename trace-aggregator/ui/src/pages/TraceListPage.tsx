import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { api } from "../api";
import type { TraceSummary } from "../types";
import { fmtDateTime, fmtMs, fmtTokens, shortId } from "../utils/format";

type Filter = "all" | "errors" | "clean";

export function TraceListPage() {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    let cancel = false;
    const hasErrors = filter === "errors" ? true : filter === "clean" ? false : undefined;

    const fetch = () => {
      setError(null);
      api
        .listTraces(50, hasErrors)
        .then((res) => {
          if (cancel) return;
          setTraces(res.items);              // ← unwrap items
          setNextCursor(res.next_cursor);     // ← capture cursor
          setHasMore(res.has_more);           // ← capture has_more
          setLoading(false);
        })
        .catch((e) => {
          if (!cancel) {
            setError(String(e));
            setLoading(false);
          }
        });
    };

    setLoading(true);
    fetch();
    // Live refresh every 5s (will reset back to page 1 — intended)
    const interval = setInterval(fetch, 5000);
    return () => {
      cancel = true;
      clearInterval(interval);
    };
  }, [filter]);

  // Load More button handler — appends the next page
  const loadMore = () => {
    if (!nextCursor || loadingMore) return;
    const hasErrors = filter === "errors" ? true : filter === "clean" ? false : undefined;
    setLoadingMore(true);
    api
      .listTraces(50, hasErrors, nextCursor)
      .then((res) => {
        setTraces((prev) => [...prev, ...res.items]);
        setNextCursor(res.next_cursor);
        setHasMore(res.has_more);
        setLoadingMore(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoadingMore(false);
      });
  };

  return (
    <div className="max-w-[1400px] mx-auto px-8 py-12">
      {/* Heading block — editorial */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="mb-12"
      >
        <div className="eyebrow mb-3">recent executions</div>
        <h1 className="font-display text-[64px] leading-[0.95] tracking-tightest text-cream-50">
          Pipeline <span className="italic text-cherry-light">traces</span>
        </h1>
        <p className="mt-4 font-display italic text-[18px] text-cream-300 max-w-xl">
          Every multi-agent run, captured in causal order. Click a trace to inspect
          the DAG, timeline, and per-agent blame.
        </p>
      </motion.div>

      {/* Filter row */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex gap-1 hairline rounded-sm overflow-hidden">
          {(["all", "errors", "clean"] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`px-4 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
                filter === f
                  ? "bg-cream-50 text-ink-900"
                  : "text-cream-300 hover:bg-ink-700"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px] text-cream-500">
          {loading ? "loading…" : `${traces.length} traces`}
          {!loading && (
            <span className="flex items-center gap-1 text-sage">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-sage animate-pulse" />
              live
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="hairline rounded-sm bg-cherry/10 border-cherry/40 p-4 text-cherry-light font-mono text-[12px] mb-6">
          API error: {error}. Is the FastAPI server running on :8000?
        </div>
      )}

      {!error && traces.length === 0 && !loading && (
        <div className="hairline rounded-sm p-12 text-center">
          <div className="font-display italic text-[20px] text-cream-300 mb-2">
            No traces yet.
          </div>
          <div className="font-mono text-[12px] text-cream-500">
            Run <span className="text-cream-100">python -m demo.pipeline</span> to
            emit some.
          </div>
        </div>
      )}

      {/* Table */}
      {traces.length > 0 && (
        <div className="hairline rounded-sm overflow-hidden">
          <div className="grid grid-cols-[1fr_120px_140px_120px_100px_180px] gap-4 px-5 py-3 font-mono text-[10px] uppercase tracking-[0.18em] text-cream-500 hairline-b bg-ink-700/30">
            <div>trace</div>
            <div className="text-right">spans</div>
            <div className="text-right">latency</div>
            <div className="text-right">tokens</div>
            <div className="text-right">errors</div>
            <div className="text-right">when</div>
          </div>
          {traces.map((t, i) => (
            <motion.div
              key={t.trace_id}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.35, delay: Math.min(i * 0.025, 0.6) }}
            >
              <Link
                to={`/traces/${t.trace_id}`}
                className="grid grid-cols-[1fr_120px_140px_120px_100px_180px] gap-4 px-5 py-3.5 hover:bg-ink-700/40 transition-colors items-center hairline-b last:border-b-0 group"
              >
                <div className="flex items-center gap-3 font-mono text-[12px]">
                  <span className="text-cream-300 group-hover:text-cherry-light transition-colors">
                    {shortId(t.trace_id)}
                  </span>
                  <span className="text-cream-500">{t.trace_id.slice(8, 16)}…</span>
                </div>
                <div className="text-right font-mono text-[12px] text-cream-100 tabular-nums">
                  {t.span_count}
                </div>
                <div className="text-right font-mono text-[12px] text-cream-100 tabular-nums">
                  {fmtMs(t.total_latency_ms)}
                </div>
                <div className="text-right font-mono text-[12px] text-cream-100 tabular-nums">
                  {fmtTokens(t.total_input_tokens + t.total_output_tokens)}
                </div>
                <div className="text-right font-mono text-[12px] tabular-nums">
                  {t.error_count > 0 ? (
                    <span className="text-cherry-light">{t.error_count}</span>
                  ) : (
                    <span className="text-cream-500">—</span>
                  )}
                </div>
                <div
                  className="text-right font-mono text-[11px] text-cream-500 whitespace-nowrap"
                  title={t.reconstructed_at}
                >
                  {fmtDateTime(t.reconstructed_at)}
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      )}
      {/* Load More button — only shown when there are more pages */}
      {hasMore && (
        <div className="mt-6 flex justify-center">
          <button
            type="button"
            onClick={loadMore}
            disabled={loadingMore}
            className="hairline rounded-sm px-6 py-2 font-mono text-[11px] uppercase tracking-wider text-cream-300 hover:bg-ink-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loadingMore ? "loading…" : "load more"}
          </button>
        </div>
      )}
    </div>
  );
}

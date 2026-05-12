import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { motion } from "framer-motion";
import { api, openTraceStream } from "../api";
import type { TraceSummary } from "../types";
import { fmtDateTime, fmtMs, fmtTokens, shortId } from "../utils/format";

type Filter = "all" | "errors" | "clean";

export function TraceListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const agentId = searchParams.get("agent_id") || undefined;

  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  // NEW state for cursor pagination
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const [live, setLive] = useState(true);   // toggle SSE vs polling
  const [openIncidents, setOpenIncidents] = useState(0);

  useEffect(() => {
    let cancel = false;
    const tick = () =>
      api.listIncidents("open")
        .then((res) => { if (!cancel) setOpenIncidents(res.items.length); })
        .catch(() => {});
    tick();
    const t = setInterval(tick, 10_000);
    return () => { cancel = true; clearInterval(t); };
  }, []);

  const clearAgentFilter = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("agent_id");
    setSearchParams(next);
  };

  useEffect(() => {
    let cancel = false;
    const hasErrors = filter === "errors" ? true : filter === "clean" ? false : undefined;

    // Initial fetch: always populate the table with the latest traces.
    setLoading(true);
    setError(null);
    api
      .listTraces(50, hasErrors, undefined, agentId)
      .then((rows) => {
        if (cancel) return;
        setTraces(rows.items);
        setNextCursor(rows.next_cursor);
        setHasMore(rows.has_more);
        setLoading(false);
      })
      .catch((e) => {
        if (!cancel) {
          setError(String(e));
          setLoading(false);
        }
      });

    if (live) {
      const close = openTraceStream({
        hasErrors,
        agentId,
        onTrace: (t) => {
          setTraces((prev) => {
            const i = prev.findIndex((x) => x.trace_id === t.trace_id);
            if (i >= 0) {
              const next = [...prev];
              next[i] = t;
              return next;
            }
            return [t, ...prev].slice(0, 500);
          });
        },
        onError: (msg) => {
          console.warn("[SSE]", msg);
        },
      });
      return () => {
        cancel = true;
        close();
      };
    } else {
      const interval = setInterval(() => {
        api
          .listTraces(50, hasErrors, undefined, agentId)
          .then((rows) => { if (!cancel) setTraces(rows.items); })
          .catch(() => {});
      }, 5000);
      return () => {
        cancel = true;
        clearInterval(interval);
      };
    }
  }, [filter, live, agentId]);

    // Load More button handler — appends the next page
  const loadMore = () => {
    if (!nextCursor || loadingMore) return;
    const hasErrors = filter === "errors" ? true : filter === "clean" ? false : undefined;
    setLoadingMore(true);
    api
      .listTraces(50, hasErrors, nextCursor, agentId)
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

      {/* Open incidents banner (ENG-11) */}
      {openIncidents > 0 && (
        <div className="mb-6 flex items-center gap-3 hairline rounded-sm bg-cherry/10 border-cherry/40 px-4 py-3">
          <span className="inline-block w-2 h-2 rounded-full bg-cherry animate-pulse" />
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-cherry-light">
            {openIncidents} open incident{openIncidents > 1 ? "s" : ""}
          </span>
          <Link
            to="/incidents"
            className="ml-auto font-mono text-[11px] uppercase tracking-wider text-cream-300 hover:text-cream-50 hairline rounded-sm px-3 py-1 hover:bg-ink-700 transition-colors"
          >
            view all →
          </Link>
        </div>
      )}

      {/* Agent filter badge (drill-down from /blame) */}
      {agentId && (
        <div className="mb-6 flex items-center gap-3 hairline rounded-sm bg-cherry/10 border-cherry/40 px-4 py-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-cherry-light">
            filtered by agent
          </span>
          <span className="font-display italic text-[18px] text-cream-50">
            {agentId}
          </span>
          <button
            type="button"
            onClick={clearAgentFilter}
            className="ml-auto font-mono text-[11px] uppercase tracking-wider text-cream-300 hover:text-cream-50 hairline rounded-sm px-3 py-1 hover:bg-ink-700 transition-colors"
          >
            clear ✕
          </button>
        </div>
      )}

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
        <div className="flex items-center gap-3 font-mono text-[11px] text-cream-500">
          {loading ? "loading…" : `${traces.length} traces`}

          {/* Live / paused toggle */}
          <button
            type="button"
            onClick={() => setLive((l) => !l)}
            className={`hairline rounded-sm px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors flex items-center ${
              live
                ? "bg-sage/20 text-sage border-sage/40"
                : "text-cream-300 hover:bg-ink-700"
            }`}
            title={live ? "Live updates via SSE — click to pause" : "Paused — click to resume live updates"}
          >
            <span
              className={`inline-block w-1.5 h-1.5 rounded-full mr-2 ${
                live ? "bg-sage animate-pulse" : "bg-cream-500"
              }`}
            />
            {live ? "live" : "paused"}
          </button>
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
      {hasMore && !live &&(
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
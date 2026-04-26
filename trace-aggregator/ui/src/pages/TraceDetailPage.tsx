import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { api } from "../api";
import type { TraceDetail, RootCauseEdge } from "../types";
import { fmtMs, fmtTokens, shortId } from "../utils/format";
import { StatTile } from "../components/StatTile";
import { TimelineWaterfall } from "../components/TimelineWaterfall";
import { DAGView } from "../components/DAGView";
import { BlamePanel } from "../components/BlamePanel";
import { DecisionPanel } from "../components/DecisionPanel";

export function TraceDetailPage() {
  const { id = "" } = useParams();
  const [data, setData] = useState<TraceDetail | null>(null);
  const [rootCause, setRootCause] = useState<RootCauseEdge[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setRootCause([]);
    setError(null);
    api.getTrace(id).then(setData).catch((e) => setError(String(e)));
    api.getRootCause(id).then(setRootCause).catch(() => setRootCause([]));
  }, [id]);

  if (error) {
    return (
      <div className="max-w-[1400px] mx-auto px-8 py-12">
        <Link
          to="/"
          className="font-mono text-[11px] uppercase tracking-wider text-cream-500 hover:text-cream-100"
        >
          ← back to traces
        </Link>
        <div className="hairline rounded-sm bg-cherry/10 border-cherry/40 p-4 text-cherry-light font-mono text-[12px] mt-6">
          {error}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="max-w-[1400px] mx-auto px-8 py-12">
        <div className="font-mono text-[12px] text-cream-500 animate-pulse">
          Loading trace…
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[1400px] mx-auto px-8 py-10">
      <Link
        to="/"
        className="inline-flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-cream-500 hover:text-cream-100 transition-colors mb-6"
      >
        ← back to traces
      </Link>

      {/* Header block */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="mb-10 flex items-end justify-between flex-wrap gap-4"
      >
        <div>
          <div className="eyebrow mb-2">trace</div>
          <h1 className="font-display text-[42px] leading-none tracking-tightest text-cream-50 mb-2">
            <span className="italic">execution</span>{" "}
            <span className="font-mono text-[28px] text-cream-300">
              {shortId(data.trace_id)}
            </span>
          </h1>
          <div className="font-mono text-[11px] text-cream-500">{data.trace_id}</div>
        </div>

        {data.error_count > 0 && (
          <div className="hairline border-cherry/50 bg-cherry/10 px-4 py-2 rounded-sm">
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-cherry-light">
              {data.error_count} error{data.error_count > 1 ? "s" : ""} detected
            </div>
          </div>
        )}
      </motion.div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-10">
        <StatTile label="spans" value={data.span_count} unit="captured" />
        <StatTile label="total latency" value={fmtMs(data.total_latency_ms)} />
        <StatTile
          label="tokens"
          value={fmtTokens(data.total_input_tokens + data.total_output_tokens)}
        />
        <StatTile
          label="errors"
          value={data.error_count}
          tone={data.error_count > 0 ? "alert" : "default"}
        />
        <StatTile
          label="decisions"
          value={data.decision_count}
          unit="recorded"
        />
      </div>

      {/* Two-column layout — timeline + DAG + decisions on left, blame on right */}
      <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
        <div className="space-y-6">
          {/* Timeline panel */}
          <Panel
            eyebrow="waterfall"
            title="Timeline"
            subtitle="Spans ordered by start time. Hover for detail."
          >
            <TimelineWaterfall nodes={data.dag.nodes} />
          </Panel>

          {/* DAG panel */}
          <Panel
            eyebrow="causal graph"
            title="DAG"
            subtitle={
              data.dag.inferred_parents.length > 0
                ? `${data.dag.inferred_parents.length} parent${
                    data.dag.inferred_parents.length > 1 ? "s" : ""
                  } inferred via vector clock`
                : "Reconstructed from explicit parent links."
            }
          >
            <DAGView nodes={data.dag.nodes} />
          </Panel>

          {/* Decision chain panel */}
          <Panel
            eyebrow="decision chain"
            title="Decisions"
            subtitle="Agent choices and their downstream causal impact."
          >
            <DecisionPanel
              decisions={data.decisions ?? []}
              rootCause={rootCause}
            />
          </Panel>
        </div>

        <div>
          <Panel
            eyebrow="attribution"
            title="Blame"
            subtitle="Per-agent contribution to latency, tokens, and errors."
          >
            <BlamePanel rows={data.blame} />
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Panel({
  eyebrow,
  title,
  subtitle,
  children,
}: {
  eyebrow: string;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="hairline rounded-sm bg-ink-700/30 p-6"
    >
      <header className="mb-5 flex items-baseline justify-between gap-4">
        <div>
          <div className="eyebrow mb-1">{eyebrow}</div>
          <h2 className="font-display italic text-[24px] leading-none tracking-tighter text-cream-50">
            {title}
          </h2>
        </div>
        {subtitle && (
          <div className="font-mono text-[11px] text-cream-500 text-right max-w-[300px]">
            {subtitle}
          </div>
        )}
      </header>
      {children}
    </motion.section>
  );
}

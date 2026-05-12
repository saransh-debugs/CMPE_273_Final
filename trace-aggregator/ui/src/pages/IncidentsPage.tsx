import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../api";
import type { Incident, IncidentState } from "../types";

const tabs: { id: IncidentState | "all"; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "ack", label: "Ack" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];

export function IncidentsPage() {
  const [tab, setTab] = useState<IncidentState | "all">("open");
  const [items, setItems] = useState<Incident[]>([]);
  const [counts, setCounts] = useState({ open: 0, ack: 0, resolved: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .listIncidents(tab === "all" ? undefined : tab)
      .then((res) => {
        setItems(res.items);
        setCounts(res.counts);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, [tab]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  const onAck = async (key: string) => {
    try { await api.ackIncident(key); refresh(); } catch (e) { setError(String(e)); }
  };
  const onResolve = async (key: string) => {
    try { await api.resolveIncident(key); refresh(); } catch (e) { setError(String(e)); }
  };

  return (
    <div className="max-w-[1100px] mx-auto px-8 py-12">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="mb-10"
      >
        <div className="eyebrow mb-3">incident model</div>
        <h1 className="font-display text-[64px] leading-[0.95] tracking-tightest text-cream-50">
          Active <span className="italic text-cherry-light">incidents</span>
        </h1>
        <p className="mt-4 font-display italic text-[18px] text-cream-300 max-w-xl">
          What's currently on fire, and what's smouldering. Ack to silence; resolve to close.
        </p>
      </motion.div>

      {/* Tab filter */}
      <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
        <div className="flex gap-1 hairline rounded-sm overflow-hidden">
          {tabs.map((t) => {
            const n = t.id === "all" ? counts.open + counts.ack + counts.resolved : counts[t.id];
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={`px-4 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
                  tab === t.id ? "bg-cream-50 text-ink-900" : "text-cream-300 hover:bg-ink-700"
                }`}
              >
                {t.label} ({n})
              </button>
            );
          })}
        </div>
        <div className="font-mono text-[11px] text-cream-500">
          {loading ? "loading…" : `${items.length} shown`}
        </div>
      </div>

      {error && (
        <div className="hairline rounded-sm bg-cherry/10 border-cherry/40 p-4 text-cherry-light font-mono text-[12px] mb-6">
          API error: {error}
        </div>
      )}

      {!error && !loading && items.length === 0 && (
        <div className="hairline rounded-sm p-12 text-center">
          <div className="font-display italic text-[20px] text-cream-300 mb-2">
            Nothing on fire.{tab === "open" ? " (Lucky day.)" : ""}
          </div>
        </div>
      )}

      <div className="space-y-3">
        {items.map((inc, i) => (
          <motion.div
            key={inc.incident_key}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3, delay: Math.min(i * 0.03, 0.4) }}
            className={`hairline rounded-sm bg-ink-700/30 p-5 border-l-4 ${severityBorder(inc.severity)}`}
          >
            <div className="flex items-start justify-between gap-4 mb-3">
              <div>
                <div className="flex items-center gap-3 mb-1">
                  <span className={`font-mono text-[10px] uppercase tracking-[0.18em] ${stateBadge(inc.state)}`}>
                    {inc.state}
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-cream-500">
                    {inc.alert_type}
                  </span>
                  <span className="font-mono text-[10px] text-cream-500">
                    × {inc.occurrence_count}
                  </span>
                </div>
                <div className="font-display italic text-[18px] text-cream-50">
                  {inc.message}
                </div>
                <div className="mt-2 font-mono text-[11px] text-cream-500 flex gap-4 flex-wrap">
                  <span>opened {fmtAgo(inc.opened_at)}</span>
                  <span>last seen {fmtAgo(inc.last_seen_at)}</span>
                  {inc.acknowledged_at && <span>ack'd {fmtAgo(inc.acknowledged_at)}</span>}
                  {inc.resolved_at && <span>resolved {fmtAgo(inc.resolved_at)}</span>}
                </div>
              </div>
              {inc.state !== "resolved" && (
                <div className="flex gap-2 shrink-0">
                  {inc.state === "open" && (
                    <button
                      type="button"
                      onClick={() => onAck(inc.incident_key)}
                      className="hairline rounded-sm px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-cream-300 hover:bg-ink-700 transition-colors"
                    >
                      ack
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => onResolve(inc.incident_key)}
                    className="hairline rounded-sm px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-sage hover:bg-sage/10 transition-colors"
                  >
                    resolve
                  </button>
                </div>
              )}
            </div>
            <div className="font-mono text-[10px] text-cream-500/70 truncate">
              key: {inc.incident_key}
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

function severityBorder(sev: string): string {
  if (sev === "high") return "border-l-cherry";
  if (sev === "medium") return "border-l-amber";
  return "border-l-cream-500";
}

function stateBadge(state: string): string {
  if (state === "open") return "text-cherry-light";
  if (state === "ack") return "text-amber";
  return "text-sage";
}

function fmtAgo(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "in the future";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

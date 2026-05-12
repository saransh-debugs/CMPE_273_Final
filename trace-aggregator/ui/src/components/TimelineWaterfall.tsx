import { useMemo, useState, useRef } from "react";
import type { DAGNode } from "../types";
import { agentColor, fmtMs, shortId } from "../utils/format";

interface Props {
  nodes: DAGNode[];
  selectedSpanId?: string | null;
  onSpanSelect?: (id: string | null) => void;
}

const ROW_HEIGHT = 28;
const ROW_GAP = 4;
const LEFT_LABEL_WIDTH = 200;
const RIGHT_PAD = 24;
const AXIS_HEIGHT = 24;

/**
 * Waterfall Gantt view of a trace.
 * - Each row is one span, ordered by start time.
 * - X axis is wall time (ms since trace start).
 * - Bar length = latency_ms. Bar color = stable per-agent color.
 * - Error spans get a cherry-red border + slash pattern.
 */
export function TimelineWaterfall({ nodes, selectedSpanId, onSpanSelect }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [mouse, setMouse] = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);

  const { rows, span, t0, totalMs, ticks } = useMemo(() => {
    if (nodes.length === 0) {
      return { rows: [], span: 0, t0: 0, totalMs: 0, ticks: [] as number[] };
    }
    const sorted = [...nodes].sort((a, b) => a.start_time_ms - b.start_time_ms);
    const t0 = sorted[0].start_time_ms;
    const totalMs = Math.max(
      ...sorted.map((n) => n.start_time_ms + n.latency_ms - t0),
      1
    );

    // Choose ~5 ticks
    const niceStep = niceTickStep(totalMs, 5);
    const ticks: number[] = [];
    for (let t = 0; t <= totalMs; t += niceStep) ticks.push(t);

    return { rows: sorted, span: sorted.length, t0, totalMs, ticks };
  }, [nodes]);

  if (rows.length === 0) {
    return (
      <div className="text-cream-500 italic font-display p-8">
        No spans recorded for this trace.
      </div>
    );
  }

  const height = AXIS_HEIGHT + span * (ROW_HEIGHT + ROW_GAP);
  const totalWidth = 1000; // fluid via viewBox
  const plotWidth = totalWidth - LEFT_LABEL_WIDTH - RIGHT_PAD;
  const xScale = (ms: number) =>
    LEFT_LABEL_WIDTH + (ms / totalMs) * plotWidth;

  return (
    <div
      ref={containerRef}
      className="relative w-full overflow-x-auto"
      onMouseMove={(e) => {
        const rect = containerRef.current?.getBoundingClientRect();
        if (rect) setMouse({ x: e.clientX - rect.left, y: e.clientY - rect.top });
      }}
    >
      <svg
        viewBox={`0 0 ${totalWidth} ${height}`}
        className="w-full"
        style={{ minWidth: 600, height }}
      >
        <defs>
          <pattern
            id="error-hatch"
            patternUnits="userSpaceOnUse"
            width="6"
            height="6"
            patternTransform="rotate(45)"
          >
            <line x1="0" y1="0" x2="0" y2="6" stroke="#C73E1D" strokeWidth="2" />
          </pattern>
        </defs>

        {/* Axis ticks + gridlines */}
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={xScale(t)}
              x2={xScale(t)}
              y1={AXIS_HEIGHT}
              y2={height}
              stroke="#2A2521"
              strokeDasharray="2 4"
            />
            <text
              x={xScale(t)}
              y={AXIS_HEIGHT - 8}
              fill="#6B6760"
              fontFamily="JetBrains Mono"
              fontSize="10"
              textAnchor="middle"
            >
              {fmtMs(t)}
            </text>
          </g>
        ))}

        {/* Rows */}
        {rows.map((n, i) => {
          const y = AXIS_HEIGHT + i * (ROW_HEIGHT + ROW_GAP);
          const x = xScale(n.start_time_ms - t0);
          // Floor the bar width at 2px so errors with 0 latency stay visible.
          const w = Math.max((n.latency_ms / totalMs) * plotWidth, 2);
          const c = agentColor(n.agent_id);
          const isError = n.event_type === "error";
          const isHovered = hovered === n.span_id;
          const isSelected = selectedSpanId === n.span_id;

          return (
            <g
              key={n.span_id}
              onMouseEnter={() => setHovered(n.span_id)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => onSpanSelect?.(isSelected ? null : n.span_id)}
              style={{ cursor: "pointer" }}
            >
              {/* Row hover/select background */}
              <rect
                x={0}
                y={y - 2}
                width={totalWidth}
                height={ROW_HEIGHT}
                fill={isSelected ? "#2A1F1C" : isHovered ? "#1A1714" : "transparent"}
              />
              {/* Agent label */}
              <text
                x={LEFT_LABEL_WIDTH - 16}
                y={y + ROW_HEIGHT / 2 + 4}
                fill="#E8E2D4"
                fontFamily="JetBrains Mono"
                fontSize="11"
                textAnchor="end"
              >
                {n.agent_id}
              </text>
              {/* Vector clock height marker — small dot */}
              <circle
                cx={LEFT_LABEL_WIDTH - 6}
                cy={y + ROW_HEIGHT / 2}
                r="2"
                fill={c}
              />
              {/* The bar */}
              <rect
                x={x}
                y={y}
                width={w}
                height={ROW_HEIGHT - 6}
                rx={2}
                fill={isError ? "url(#error-hatch)" : c}
                stroke={isError ? "#C73E1D" : c}
                strokeWidth={isError ? 1.5 : 0}
                opacity={isHovered ? 1 : 0.85}
              />
              {/* Selection ring */}
              {isSelected && (
                <rect
                  x={x - 2}
                  y={y - 2}
                  width={w + 4}
                  height={ROW_HEIGHT - 2}
                  rx={3}
                  fill="none"
                  stroke="#E8E2D4"
                  strokeWidth={1.5}
                />
              )}
              {/* Inline label inside the bar if it fits */}
              {w > 80 && (
                <text
                  x={x + 8}
                  y={y + ROW_HEIGHT / 2 + 3}
                  fill="#0F0E0C"
                  fontFamily="JetBrains Mono"
                  fontSize="10"
                  fontWeight="600"
                >
                  {fmtMs(n.latency_ms)}
                  {n.inferred_parent && " ·  inferred"}
                </text>
              )}
              {/* Latency tail label when bar is short */}
              {w <= 80 && (
                <text
                  x={x + w + 6}
                  y={y + ROW_HEIGHT / 2 + 3}
                  fill="#A8A299"
                  fontFamily="JetBrains Mono"
                  fontSize="10"
                >
                  {fmtMs(n.latency_ms)}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {/* Hover detail — follows cursor */}
      {hovered && (() => {
        const n = rows.find((r) => r.span_id === hovered);
        if (!n) return null;
        const containerWidth = containerRef.current?.offsetWidth ?? 800;
        const tooltipWidth = 288; // w-72
        const flipX = mouse.x + tooltipWidth + 16 > containerWidth;
        const left = flipX ? mouse.x - tooltipWidth - 8 : mouse.x + 16;
        const top = Math.max(0, mouse.y - 8);
        return (
          <div
            className="absolute hairline bg-ink-700 p-4 rounded-sm w-72 text-[12px] font-mono shadow-xl pointer-events-none z-10"
            style={{ left, top }}
          >
            <div className="eyebrow mb-2">Span detail</div>
            <Row k="agent" v={n.agent_id} accent />
            <Row k="span_id" v={shortId(n.span_id)} />
            <Row k="parent" v={n.parent_span_id ? shortId(n.parent_span_id) : "—"} />
            <Row k="event" v={n.event_type} />
            <Row k="latency" v={fmtMs(n.latency_ms)} />
            <Row k="tokens" v={`${n.input_tokens}↓ ${n.output_tokens}↑`} />
            {n.inferred_parent && (
              <div className="mt-2 text-[10px] text-amber italic">
                parent inferred via vector clock
              </div>
            )}
            <div className="mt-2 text-[10px] text-cream-500">
              vc: {Object.entries(n.vector_clock).map(([k, v]) => `${k}=${v}`).join("  ")}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

function Row({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="flex justify-between gap-3 py-0.5">
      <span className="text-cream-500">{k}</span>
      <span className={accent ? "text-cherry-light" : "text-cream-100"}>{v}</span>
    </div>
  );
}

function niceTickStep(totalMs: number, target: number): number {
  const raw = totalMs / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  let nice;
  if (norm < 1.5) nice = 1;
  else if (norm < 3) nice = 2;
  else if (norm < 7) nice = 5;
  else nice = 10;
  return nice * mag;
}

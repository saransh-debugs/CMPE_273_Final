import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { RawSpan } from "../types";
import { agentColor } from "../utils/format";

interface SpanOutput {
  agent_id: string;
  span_id: string;
  fields: Record<string, string>;
}

function parseOutputs(spans: RawSpan[]): SpanOutput[] {
  const results: SpanOutput[] = [];
  for (const span of spans) {
    try {
      const meta = JSON.parse(span.metadata || "{}");
      const output = meta.output as Record<string, string> | undefined;
      if (output && Object.keys(output).length > 0) {
        results.push({ agent_id: span.agent_id, span_id: span.span_id, fields: output });
      }
    } catch {
      // skip unparseable spans
    }
  }
  return results;
}

const FIELD_ORDER = ["research_findings", "code", "review"];
const FIELD_LABELS: Record<string, string> = {
  research_findings: "Research",
  code: "Code",
  review: "Review",
};

function isCode(key: string, value: string): boolean {
  return key === "code" || value.trim().startsWith("def ") || value.trim().startsWith("class ");
}

function OutputField({ label, value }: { label: string; value: string }) {
  const [expanded, setExpanded] = useState(true);
  const asCode = isCode(label.toLowerCase(), value);
  return (
    <div className="hairline rounded-sm overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center justify-between px-4 py-2 bg-ink-700/40 hover:bg-ink-700/70 transition-colors"
      >
        <span className="font-mono text-[11px] uppercase tracking-wider text-fg-300">{label}</span>
        <span className="font-mono text-[11px] text-500">{expanded ? "▲" : "▼"}</span>
      </button>
      {expanded && (
        asCode ? (
          <pre className="px-4 py-3 font-mono text-[12px] text-emerald-300 bg-ink-900/60 whitespace-pre-wrap break-all leading-relaxed overflow-auto max-h-64">
            {value}
          </pre>
        ) : (
          <div className="px-4 py-3 text-[13px] text-fg-200 leading-relaxed markdown-output">
            <ReactMarkdown
              components={{
                p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                strong: ({ children }) => <strong className="text-fg-100 font-semibold">{children}</strong>,
                em: ({ children }) => <em className="text-fg-300">{children}</em>,
                ul: ({ children }) => <ul className="list-disc list-outside pl-4 mb-2 space-y-1">{children}</ul>,
                ol: ({ children }) => <ol className="list-decimal list-outside pl-4 mb-2 space-y-1">{children}</ol>,
                li: ({ children }) => <li className="text-fg-200">{children}</li>,
                h1: ({ children }) => <h1 className="font-semibold text-fg-100 text-[15px] mb-1">{children}</h1>,
                h2: ({ children }) => <h2 className="font-semibold text-fg-100 text-[14px] mb-1">{children}</h2>,
                h3: ({ children }) => <h3 className="font-semibold text-fg-100 text-[13px] mb-1">{children}</h3>,
                code: ({ children }) => (
                  <code className="font-mono text-[11px] text-emerald-300 bg-ink-900/60 px-1 py-0.5 rounded">{children}</code>
                ),
                pre: ({ children }) => (
                  <pre className="font-mono text-[11px] text-emerald-300 bg-ink-900/60 px-3 py-2 rounded overflow-auto my-2">{children}</pre>
                ),
                hr: () => <hr className="border-ink-600 my-2" />,
              }}
            >
              {value}
            </ReactMarkdown>
          </div>
        )
      )}
    </div>
  );
}

function AgentCard({ out }: { out: SpanOutput }) {
  const color = agentColor(out.agent_id);
  const orderedKeys = [
    ...FIELD_ORDER.filter((k) => k in out.fields),
    ...Object.keys(out.fields).filter((k) => !FIELD_ORDER.includes(k)),
  ];
  return (
    <div className="hairline rounded-sm overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 bg-ink-700/50">
        <span
          className="inline-block w-2 h-2 rounded-full flex-shrink-0"
          style={{ backgroundColor: color }}
        />
        <span className="font-mono text-[12px] text-fg-100">{out.agent_id}</span>
      </div>
      <div className="p-3 space-y-2">
        {orderedKeys.map((k) => (
          <OutputField
            key={k}
            label={FIELD_LABELS[k] ?? k}
            value={out.fields[k]}
          />
        ))}
      </div>
    </div>
  );
}

function FinalView({ outputs }: { outputs: SpanOutput[] }) {
  const byField: Record<string, { agent_id: string; value: string }> = {};
  for (const out of outputs) {
    for (const [k, v] of Object.entries(out.fields)) {
      if (!(k in byField)) byField[k] = { agent_id: out.agent_id, value: v };
    }
  }

  const orderedKeys = [
    ...FIELD_ORDER.filter((k) => k in byField),
    ...Object.keys(byField).filter((k) => !FIELD_ORDER.includes(k)),
  ];

  if (orderedKeys.length === 0) {
    return (
      <div className="text-500 italic font-display text-[14px]">
        No output fields captured.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {orderedKeys.map((k) => {
        const { agent_id, value } = byField[k];
        const color = agentColor(agent_id);
        return (
          <div key={k}>
            <div className="flex items-center gap-2 mb-1.5">
              <span
                className="inline-block w-1.5 h-1.5 rounded-full"
                style={{ backgroundColor: color }}
              />
              <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-500">
                {agent_id}
              </span>
            </div>
            <OutputField label={FIELD_LABELS[k] ?? k} value={value} />
          </div>
        );
      })}
    </div>
  );
}

interface Props {
  spans: RawSpan[];
}

export function PipelineOutputPanel({ spans }: Props) {
  const [tab, setTab] = useState<"per-agent" | "final">("final");
  const outputs = parseOutputs(spans);

  if (outputs.length === 0) {
    return (
      <div className="text-500 italic font-display text-[14px]">
        No output data captured. Re-run the pipeline to populate outputs.
      </div>
    );
  }

  return (
    <div>
      <div className="flex gap-1 hairline rounded-sm overflow-hidden mb-5 w-fit">
        {(["final", "per-agent"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
              tab === t ? "bg-white text-ink-900" : "text-fg-300 hover:bg-ink-700"
            }`}
          >
            {t === "final" ? "final output" : `per agent (${outputs.length})`}
          </button>
        ))}
      </div>

      {tab === "per-agent" && (
        <div className="space-y-3">
          {outputs.map((out) => (
            <AgentCard key={out.span_id} out={out} />
          ))}
        </div>
      )}

      {tab === "final" && <FinalView outputs={outputs} />}
    </div>
  );
}

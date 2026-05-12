import type { DAGNode } from "../types";
import { agentColor, fmtMs, shortId } from "../utils/format";

interface Props {
  nodes: DAGNode[];
  selectedSpanId?: string | null;
  onSpanSelect?: (id: string | null) => void;
}

/**
 * Hierarchical DAG view. Renders the causal graph as an indented tree —
 * easier to read than a force-directed blob and matches the project's
 * editorial aesthetic.
 */
export function DAGView({ nodes, selectedSpanId, onSpanSelect }: Props) {
  const byId = new Map(nodes.map((n) => [n.span_id, n]));

  // Find roots: spans no other span lists as a child.
  const childIds = new Set<string>();
  nodes.forEach((n) => n.children.forEach((c) => childIds.add(c)));
  const roots = nodes.filter((n) => !childIds.has(n.span_id));

  return (
    <div className="font-mono text-[12px] leading-relaxed">
      {roots.map((r) => (
        <TreeNode
          key={r.span_id}
          node={r}
          byId={byId}
          depth={0}
          isLast={true}
          prefix=""
          selectedSpanId={selectedSpanId}
          onSpanSelect={onSpanSelect}
        />
      ))}
      {roots.length === 0 && (
        <div className="text-cream-500 italic font-display">No root spans found.</div>
      )}
    </div>
  );
}

function TreeNode({
  node,
  byId,
  depth,
  isLast,
  prefix,
  selectedSpanId,
  onSpanSelect,
}: {
  node: DAGNode;
  byId: Map<string, DAGNode>;
  depth: number;
  isLast: boolean;
  prefix: string;
  selectedSpanId?: string | null;
  onSpanSelect?: (id: string | null) => void;
}) {
  const c = agentColor(node.agent_id);
  const isError = node.event_type === "error";
  const isSelected = selectedSpanId === node.span_id;

  // Tree connector chars — pure ASCII like git log graph.
  const connector = depth === 0 ? "" : isLast ? "└─ " : "├─ ";
  const childPrefix = prefix + (depth === 0 ? "" : isLast ? "   " : "│  ");

  return (
    <div>
      <div
        className={`group flex items-center -mx-2 px-2 rounded-sm cursor-pointer ${
          isSelected
            ? "bg-cherry/15 border-l-2 border-cherry"
            : "hover:bg-ink-700/40"
        }`}
        onClick={() => onSpanSelect?.(isSelected ? null : node.span_id)}
      >
        <span className="text-ink-500 select-none whitespace-pre">
          {prefix + connector}
        </span>
        <span
          className="inline-block w-2 h-2 rounded-full mr-2 shrink-0"
          style={{ background: c }}
        />
        <span className="text-cream-50">{node.agent_id}</span>
        {isError && (
          <span className="ml-2 text-[10px] uppercase tracking-tighter text-cherry">
            error
          </span>
        )}
        {node.inferred_parent && (
          <span className="ml-2 text-[10px] uppercase tracking-tighter text-amber italic">
            inferred
          </span>
        )}
        <span className="ml-auto text-cream-500 group-hover:text-cream-300 flex gap-3">
          <span>{fmtMs(node.latency_ms)}</span>
          <span>
            {node.input_tokens + node.output_tokens > 0
              ? `${node.input_tokens + node.output_tokens}t`
              : ""}
          </span>
          <span className="text-ink-500">{shortId(node.span_id)}</span>
        </span>
      </div>
      {node.children.map((cid, i) => {
        const child = byId.get(cid);
        if (!child) return null;
        const last = i === node.children.length - 1;
        return (
          <TreeNode
            key={cid}
            node={child}
            byId={byId}
            depth={depth + 1}
            isLast={last}
            prefix={childPrefix}
            selectedSpanId={selectedSpanId}
            onSpanSelect={onSpanSelect}
          />
        );
      })}
    </div>
  );
}

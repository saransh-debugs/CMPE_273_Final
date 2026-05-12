import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TimelineWaterfall } from "./TimelineWaterfall";
import type { DAGNode } from "../types";

const makeNode = (id: string, agentId: string, startMs = 0, latencyMs = 100): DAGNode => ({
  span_id: id,
  parent_span_id: null,
  inferred_parent: false,
  children: [],
  agent_id: agentId,
  event_type: "llm",
  vector_clock: {},
  latency_ms: latencyMs,
  input_tokens: 10,
  output_tokens: 20,
  start_time_ms: startMs,
});

describe("TimelineWaterfall", () => {
  it("renders empty state when no nodes", () => {
    const { getByText } = render(<TimelineWaterfall nodes={[]} />);
    expect(getByText(/No spans recorded/i)).toBeInTheDocument();
  });

  it("renders SVG rows for each node", () => {
    const nodes = [makeNode("s1", "agent-a"), makeNode("s2", "agent-b", 110)];
    const { container } = render(<TimelineWaterfall nodes={nodes} />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // Two agent labels rendered as text elements
    const texts = svg!.querySelectorAll("text");
    const labels = Array.from(texts).map((t) => t.textContent);
    expect(labels).toContain("agent-a");
    expect(labels).toContain("agent-b");
  });

  it("shows selection ring when selectedSpanId matches a node", () => {
    const nodes = [makeNode("s1", "agent-a")];
    const { container } = render(
      <TimelineWaterfall nodes={nodes} selectedSpanId="s1" onSpanSelect={vi.fn()} />,
    );
    // Selection ring is a rect with stroke="#E8E2D4" and fill="none"
    const ring = container.querySelector('rect[stroke="#E8E2D4"][fill="none"]');
    expect(ring).not.toBeNull();
  });

  it("does not show selection ring when selectedSpanId is null", () => {
    const nodes = [makeNode("s1", "agent-a")];
    const { container } = render(
      <TimelineWaterfall nodes={nodes} selectedSpanId={null} onSpanSelect={vi.fn()} />,
    );
    const ring = container.querySelector('rect[stroke="#E8E2D4"][fill="none"]');
    expect(ring).toBeNull();
  });

  it("calls onSpanSelect when a row is clicked", () => {
    const onSpanSelect = vi.fn();
    const nodes = [makeNode("s1", "agent-a")];
    const { container } = render(
      <TimelineWaterfall nodes={nodes} selectedSpanId={null} onSpanSelect={onSpanSelect} />,
    );
    // The clickable <g> elements are the span rows; skip the tick groups (which have no onClick)
    // Find the row g by looking for one that wraps an agent label text
    const allGs = Array.from(container.querySelectorAll("svg g"));
    // The row g contains a text with the agent_id
    const rowG = allGs.find((g) => g.textContent?.includes("agent-a"));
    expect(rowG).toBeDefined();
    fireEvent.click(rowG!);
    expect(onSpanSelect).toHaveBeenCalledWith("s1");
  });

  it("calls onSpanSelect with null when clicking already-selected row (toggle off)", () => {
    const onSpanSelect = vi.fn();
    const nodes = [makeNode("s1", "agent-a")];
    const { container } = render(
      <TimelineWaterfall nodes={nodes} selectedSpanId="s1" onSpanSelect={onSpanSelect} />,
    );
    const allGs = Array.from(container.querySelectorAll("svg g"));
    const rowG = allGs.find((g) => g.textContent?.includes("agent-a"));
    fireEvent.click(rowG!);
    expect(onSpanSelect).toHaveBeenCalledWith(null);
  });
});

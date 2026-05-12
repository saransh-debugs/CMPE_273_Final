import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DAGView } from "./DAGView";
import type { DAGNode } from "../types";

const makeNode = (id: string, agent: string, children: string[] = []): DAGNode => ({
  span_id: id,
  parent_span_id: null,
  inferred_parent: false,
  children,
  agent_id: agent,
  event_type: "llm",
  vector_clock: {},
  latency_ms: 100,
  input_tokens: 5,
  output_tokens: 10,
  start_time_ms: 0,
});

describe("DAGView (ENG-10 selection)", () => {
  it("renders empty state when no roots", () => {
    const { getByText } = render(<DAGView nodes={[]} />);
    expect(getByText(/No root spans/i)).toBeInTheDocument();
  });

  it("renders agent labels for each node", () => {
    const nodes = [
      makeNode("s1", "orchestrator", ["s2"]),
      makeNode("s2", "research_agent"),
    ];
    const { getByText } = render(<DAGView nodes={nodes} />);
    expect(getByText("orchestrator")).toBeInTheDocument();
    expect(getByText("research_agent")).toBeInTheDocument();
  });

  it("calls onSpanSelect when a node row is clicked", () => {
    const onSpanSelect = vi.fn();
    const nodes = [makeNode("s1", "agent-a")];
    const { container } = render(
      <DAGView nodes={nodes} selectedSpanId={null} onSpanSelect={onSpanSelect} />,
    );
    // The clickable row is the first .group div
    const row = container.querySelector(".group");
    expect(row).not.toBeNull();
    fireEvent.click(row!);
    expect(onSpanSelect).toHaveBeenCalledWith("s1");
  });

  it("toggles selection off when clicking already-selected node", () => {
    const onSpanSelect = vi.fn();
    const nodes = [makeNode("s1", "agent-a")];
    const { container } = render(
      <DAGView nodes={nodes} selectedSpanId="s1" onSpanSelect={onSpanSelect} />,
    );
    const row = container.querySelector(".group");
    fireEvent.click(row!);
    expect(onSpanSelect).toHaveBeenCalledWith(null);
  });

  it("applies cherry highlight class to selected node", () => {
    const nodes = [makeNode("s1", "agent-a")];
    const { container } = render(
      <DAGView nodes={nodes} selectedSpanId="s1" onSpanSelect={vi.fn()} />,
    );
    const row = container.querySelector(".group");
    expect(row?.className).toMatch(/bg-cherry/);
    expect(row?.className).toMatch(/border-cherry/);
  });

  it("does not apply highlight when selectedSpanId differs", () => {
    const nodes = [makeNode("s1", "agent-a")];
    const { container } = render(
      <DAGView nodes={nodes} selectedSpanId="other" onSpanSelect={vi.fn()} />,
    );
    const row = container.querySelector(".group");
    expect(row?.className).not.toMatch(/bg-cherry/);
  });

  it("propagates selection to nested child nodes", () => {
    const onSpanSelect = vi.fn();
    const nodes = [
      makeNode("root", "orchestrator", ["child"]),
      makeNode("child", "research_agent"),
    ];
    const { container } = render(
      <DAGView nodes={nodes} selectedSpanId="child" onSpanSelect={onSpanSelect} />,
    );
    const highlighted = container.querySelectorAll(".bg-cherry\\/15");
    expect(highlighted.length).toBeGreaterThan(0);
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DecisionPanel } from "./DecisionPanel";
import type { DecisionEvent, RootCauseEdge } from "../types";

const makeDecision = (id: string, sourceSpanId: string): DecisionEvent => ({
  decision_id: id,
  trace_id: "t1",
  source_span_id: sourceSpanId,
  actor_agent_id: "orch",
  decision_type: "route",
  selected_candidate_id: "research",
  confidence: 0.9,
  rationale_summary: "Test rationale",
  evidence_refs: [],
  candidates: [],
  timestamp_ms: 1000,
  metadata: "{}",
});

const makeEdge = (decisionId: string, sourceSpanId: string): RootCauseEdge => ({
  decision_id: decisionId,
  source_span_id: sourceSpanId,
  target_span_id: "s2",
  decision_type: "route",
  actor_agent_id: "orch",
  selected_candidate_id: "research",
  confidence: 0.9,
  rationale_summary: "",
  impact_latency_ms: 50,
  impact_tokens: 100,
  impact_error_count: 0,
  impact_score: 0.5,
  uncertainty: "low",
});

describe("DecisionPanel", () => {
  it("shows empty state when no decisions or rootCause", () => {
    render(<DecisionPanel decisions={[]} rootCause={[]} />);
    expect(screen.getByText(/No decision events recorded/i)).toBeInTheDocument();
  });

  it("highlights decision card when selectedSpanId matches source_span_id", () => {
    const { container } = render(
      <DecisionPanel
        decisions={[makeDecision("d1", "span-abc")]}
        rootCause={[]}
        selectedSpanId="span-abc"
        onSpanSelect={vi.fn()}
      />,
    );
    const card = container.querySelector(".ring-1");
    expect(card).not.toBeNull();
  });

  it("does not highlight card when selectedSpanId does not match", () => {
    const { container } = render(
      <DecisionPanel
        decisions={[makeDecision("d1", "span-abc")]}
        rootCause={[]}
        selectedSpanId="span-other"
        onSpanSelect={vi.fn()}
      />,
    );
    const card = container.querySelector(".ring-1");
    expect(card).toBeNull();
  });

  it("calls onSpanSelect with span id when ↗ span chip is clicked", () => {
    const onSpanSelect = vi.fn();
    render(
      <DecisionPanel
        decisions={[makeDecision("d1", "span-abc")]}
        rootCause={[]}
        selectedSpanId={null}
        onSpanSelect={onSpanSelect}
      />,
    );
    // Open the card first to expose the footer
    const expandBtn = screen.getByRole("button", { name: /orch/i });
    fireEvent.click(expandBtn);
    const spanChip = screen.getByRole("button", { name: /↗ span/i });
    fireEvent.click(spanChip);
    expect(onSpanSelect).toHaveBeenCalledWith("span-abc");
  });

  it("calls onSpanSelect with null when clicking ↗ chip on already-selected span (toggle off)", () => {
    const onSpanSelect = vi.fn();
    render(
      <DecisionPanel
        decisions={[makeDecision("d1", "span-abc")]}
        rootCause={[]}
        selectedSpanId="span-abc"
        onSpanSelect={onSpanSelect}
      />,
    );
    const expandBtn = screen.getByRole("button", { name: /orch/i });
    fireEvent.click(expandBtn);
    const spanChip = screen.getByRole("button", { name: /↗ span/i });
    fireEvent.click(spanChip);
    expect(onSpanSelect).toHaveBeenCalledWith(null);
  });

  it("highlights root-cause card when selectedSpanId matches source_span_id", () => {
    const { container } = render(
      <DecisionPanel
        decisions={[makeDecision("d1", "span-abc")]}
        rootCause={[makeEdge("d1", "span-abc")]}
        selectedSpanId="span-abc"
        onSpanSelect={vi.fn()}
      />,
    );
    // Switch to root-cause tab
    fireEvent.click(screen.getByRole("button", { name: /root-cause/i }));
    const highlighted = container.querySelector(".ring-1");
    expect(highlighted).not.toBeNull();
  });
});

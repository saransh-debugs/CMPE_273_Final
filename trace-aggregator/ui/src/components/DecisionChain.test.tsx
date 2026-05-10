import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DecisionChain } from "./DecisionChain";
import type { DecisionEvent, RootCauseEdge } from "../types";

describe("DecisionChain", () => {
  it("shows empty state when no decisions", () => {
    render(<DecisionChain decisions={[]} rootCause={[]} />);
    expect(screen.getByText(/No decision events captured/i)).toBeInTheDocument();
  });

  it("renders decision rows and toggles selection", () => {
    const decisions: DecisionEvent[] = [
      {
        decision_id: "d1",
        trace_id: "t1",
        source_span_id: "s0",
        decision_type: "route",
        actor_agent_id: "orch",
        selected_candidate_id: "research",
        confidence: 0.85,
        rationale_summary: "Need findings first.",
        evidence_refs: ["e1"],
        metadata: JSON.stringify({ reasoning: "Because the task is open-ended." }),
        candidates: [],
        timestamp_ms: 1000,
      },
    ];
    const rootCause: RootCauseEdge[] = [
      {
        decision_id: "d1",
        source_span_id: "s0",
        target_span_id: "s1",
        decision_type: "route",
        actor_agent_id: "orch",
        selected_candidate_id: "research",
        confidence: 0.85,
        rationale_summary: "",
        impact_latency_ms: 12,
        impact_tokens: 100,
        impact_error_count: 0,
        impact_score: 0.5,
        uncertainty: "low",
        chain_rank: 0,
      },
    ];
    const onSelect = vi.fn();
    render(
      <DecisionChain
        decisions={decisions}
        rootCause={rootCause}
        selectedDecisionId={null}
        onSelectDecision={onSelect}
      />,
    );
    expect(screen.getByText(/orch\s*->\s*research/)).toBeInTheDocument();
    expect(screen.getByText(/Need findings first/)).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: /orch/i });
    fireEvent.click(btn);
    expect(onSelect).toHaveBeenCalledWith("d1");
  });
});

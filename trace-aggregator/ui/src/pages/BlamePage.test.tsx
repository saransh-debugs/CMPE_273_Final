import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { BlamePage } from "./BlamePage";
import type { GlobalBlameRow } from "../types";

const v1Row = (override: Partial<GlobalBlameRow> = {}): GlobalBlameRow => ({
  agent_id: "coder_agent",
  trace_count: 7,
  avg_blame_score: 42.0,
  total_latency_ms: 3500,
  total_input_tokens: 1500,
  total_output_tokens: 1200,
  error_count: 2,
  ...override,
});

const v2Row = (override: Partial<GlobalBlameRow> = {}): GlobalBlameRow => ({
  ...v1Row(),
  avg_blame_score_ci_low: 28.5,
  avg_blame_score_ci_high: 56.2,
  avg_blame_score_std: 12.4,
  avg_error_amplification: 1.3,
  model_version: "v2.0",
  ...override,
});

vi.mock("../api", () => ({
  api: { globalBlame: vi.fn() },
}));

import { api } from "../api";

const wrap = (ui: React.ReactNode) => <MemoryRouter>{ui}</MemoryRouter>;

describe("BlamePage (post-rewrite + ENG-06 V2 surfacing)", () => {
  beforeEach(() => {
    vi.mocked(api.globalBlame).mockReset();
  });

  it("unwraps the {agents} response shape correctly (V1 default)", async () => {
    vi.mocked(api.globalBlame).mockResolvedValueOnce({
      hours: 24,
      model_version: "v1",
      agents: [v1Row()],
    });
    render(wrap(<BlamePage />));
    await waitFor(() => expect(screen.getByText("coder_agent")).toBeInTheDocument());
    expect(api.globalBlame).toHaveBeenCalledWith(24, "v1");
  });

  it("renders V1 by default — no CI bounds row visible", async () => {
    vi.mocked(api.globalBlame).mockResolvedValueOnce({
      hours: 24,
      model_version: "v1",
      agents: [v1Row()],
    });
    render(wrap(<BlamePage />));
    await waitFor(() => screen.getByText("coder_agent"));
    expect(screen.queryByText(/std σ/i)).toBeNull();
    expect(screen.queryByText(/err amp/i)).toBeNull();
  });

  it("renders V2 fields when V2 toggle clicked", async () => {
    vi.mocked(api.globalBlame).mockResolvedValueOnce({
      hours: 24,
      model_version: "v1",
      agents: [v1Row()],
    });
    vi.mocked(api.globalBlame).mockResolvedValueOnce({
      hours: 24,
      model_version: "v2",
      agents: [v2Row()],
    });

    render(wrap(<BlamePage />));
    await waitFor(() => screen.getByText("coder_agent"));

    fireEvent.click(screen.getByRole("button", { name: /^v2$/i }));
    await waitFor(() => expect(api.globalBlame).toHaveBeenCalledWith(24, "v2"));

    await waitFor(() => {
      expect(screen.getByText(/std σ/i)).toBeInTheDocument();
      expect(screen.getByText(/err amp/i)).toBeInTheDocument();
    });
  });

  it("renders drill-down link to /?agent_id=<agent>", async () => {
    vi.mocked(api.globalBlame).mockResolvedValueOnce({
      hours: 24,
      model_version: "v1",
      agents: [v1Row({ agent_id: "research_agent" })],
    });
    render(wrap(<BlamePage />));
    await waitFor(() => screen.getByText("research_agent"));

    const link = screen.getByRole("link", { name: /research_agent/i });
    expect(link.getAttribute("href")).toBe("/?agent_id=research_agent");
  });

  it("switches time window via 1h/6h/24h/7d buttons", async () => {
    vi.mocked(api.globalBlame).mockResolvedValue({
      hours: 24,
      model_version: "v1",
      agents: [],
    });
    render(wrap(<BlamePage />));
    await waitFor(() => expect(api.globalBlame).toHaveBeenCalledWith(24, "v1"));

    fireEvent.click(screen.getByRole("button", { name: /^7d$/i }));
    await waitFor(() => expect(api.globalBlame).toHaveBeenCalledWith(168, "v1"));
  });

  it("shows empty state when no agents", async () => {
    vi.mocked(api.globalBlame).mockResolvedValueOnce({
      hours: 24,
      model_version: "v1",
      agents: [],
    });
    render(wrap(<BlamePage />));
    await waitFor(() => {
      expect(screen.getByText(/No agent data yet/i)).toBeInTheDocument();
    });
  });
});

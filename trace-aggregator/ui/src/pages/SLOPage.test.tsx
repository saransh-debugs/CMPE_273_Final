import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SLOPage } from "./SLOPage";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    slo: vi.fn(),
    health: vi.fn(),
    listTraces: vi.fn(),
    getTrace: vi.fn(),
    getDecisions: vi.fn(),
    getRootCause: vi.fn(),
    globalBlame: vi.fn(),
  },
}));

describe("SLOPage", () => {
  beforeEach(() => {
    vi.mocked(api.slo).mockResolvedValue({
      overall: "pass",
      statuses: [
        {
          name: "ingest_acceptance",
          title: "Ingest acceptance rate",
          signal: "ingest_acceptance",
          window_minutes: 15,
          threshold: 0.999,
          comparison: ">=",
          unit: "ratio",
          value: 1,
          passing: true,
          sample_count: 10,
          notes: "",
        },
      ],
      history: {},
    });
  });

  it("renders loaded SLO rows from api", async () => {
    render(<SLOPage />);
    expect(await screen.findByText(/1 SLOs evaluated/i)).toBeInTheDocument();
    expect(screen.getByText("Ingest acceptance rate")).toBeInTheDocument();
    expect(screen.getByText(/ingest_acceptance/)).toBeInTheDocument();
  });
});

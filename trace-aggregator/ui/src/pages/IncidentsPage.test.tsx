import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { IncidentsPage } from "./IncidentsPage";
import type { Incident } from "../types";

const makeIncident = (
  override: Partial<Incident> = {},
): Incident => ({
  incident_key: "error_burst:agent:foo",
  alert_type: "error_burst",
  state: "open",
  severity: "medium",
  message: "Agent foo emitted 5 errors",
  details: { agent_id: "foo" },
  opened_at: new Date(Date.now() - 60_000).toISOString(),
  last_seen_at: new Date(Date.now() - 10_000).toISOString(),
  acknowledged_at: null,
  resolved_at: null,
  occurrence_count: 3,
  ...override,
});

vi.mock("../api", () => ({
  api: {
    listIncidents: vi.fn(),
    ackIncident: vi.fn(),
    resolveIncident: vi.fn(),
  },
}));

import { api } from "../api";

describe("IncidentsPage (ENG-11)", () => {
  beforeEach(() => {
    vi.mocked(api.listIncidents).mockReset();
    vi.mocked(api.ackIncident).mockReset();
    vi.mocked(api.resolveIncident).mockReset();
  });

  it("renders empty state when no open incidents", async () => {
    vi.mocked(api.listIncidents).mockResolvedValueOnce({
      items: [],
      counts: { open: 0, ack: 0, resolved: 0 },
    });
    render(<IncidentsPage />);
    await waitFor(() => {
      expect(screen.getByText(/Nothing on fire/i)).toBeInTheDocument();
    });
  });

  it("renders incident card with message and occurrence count", async () => {
    const inc = makeIncident({ message: "Custom alert message", occurrence_count: 7 });
    vi.mocked(api.listIncidents).mockResolvedValueOnce({
      items: [inc],
      counts: { open: 1, ack: 0, resolved: 0 },
    });
    render(<IncidentsPage />);
    await waitFor(() => {
      expect(screen.getByText("Custom alert message")).toBeInTheDocument();
    });
    expect(screen.getByText(/× 7/)).toBeInTheDocument();
  });

  it("calls ackIncident when ack button clicked", async () => {
    const inc = makeIncident();
    vi.mocked(api.listIncidents).mockResolvedValue({
      items: [inc],
      counts: { open: 1, ack: 0, resolved: 0 },
    });
    vi.mocked(api.ackIncident).mockResolvedValueOnce({ ...inc, state: "ack" });

    render(<IncidentsPage />);
    await waitFor(() => screen.getByRole("button", { name: /^ack$/i }));

    fireEvent.click(screen.getByRole("button", { name: /^ack$/i }));
    await waitFor(() => {
      expect(api.ackIncident).toHaveBeenCalledWith("error_burst:agent:foo");
    });
  });

  it("calls resolveIncident when resolve button clicked", async () => {
    const inc = makeIncident();
    vi.mocked(api.listIncidents).mockResolvedValue({
      items: [inc],
      counts: { open: 1, ack: 0, resolved: 0 },
    });
    vi.mocked(api.resolveIncident).mockResolvedValueOnce({ ...inc, state: "resolved" });

    render(<IncidentsPage />);
    await waitFor(() => screen.getByRole("button", { name: /^resolve$/i }));

    fireEvent.click(screen.getByRole("button", { name: /^resolve$/i }));
    await waitFor(() => {
      expect(api.resolveIncident).toHaveBeenCalledWith("error_burst:agent:foo");
    });
  });

  it("does not show ack/resolve buttons for resolved incidents", async () => {
    const inc = makeIncident({
      state: "resolved",
      resolved_at: new Date().toISOString(),
    });
    vi.mocked(api.listIncidents).mockResolvedValueOnce({
      items: [inc],
      counts: { open: 0, ack: 0, resolved: 1 },
    });
    render(<IncidentsPage />);
    await waitFor(() => screen.getByText(inc.message));

    expect(screen.queryByRole("button", { name: /^ack$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^resolve$/i })).toBeNull();
  });

  it("hides ack button (but shows resolve) for incidents already in ack state", async () => {
    const inc = makeIncident({
      state: "ack",
      acknowledged_at: new Date().toISOString(),
    });
    vi.mocked(api.listIncidents).mockResolvedValueOnce({
      items: [inc],
      counts: { open: 0, ack: 1, resolved: 0 },
    });
    render(<IncidentsPage />);
    await waitFor(() => screen.getByText(inc.message));

    expect(screen.queryByRole("button", { name: /^ack$/i })).toBeNull();
    expect(screen.getByRole("button", { name: /^resolve$/i })).toBeInTheDocument();
  });

  it("switches tab filter when clicked", async () => {
    vi.mocked(api.listIncidents).mockResolvedValue({
      items: [],
      counts: { open: 0, ack: 0, resolved: 0 },
    });
    render(<IncidentsPage />);
    await waitFor(() => expect(api.listIncidents).toHaveBeenCalledWith("open"));

    fireEvent.click(screen.getByRole("button", { name: /Resolved/i }));
    await waitFor(() => expect(api.listIncidents).toHaveBeenCalledWith("resolved"));
  });

  it("passes undefined state when 'all' tab is selected", async () => {
    vi.mocked(api.listIncidents).mockResolvedValue({
      items: [],
      counts: { open: 0, ack: 0, resolved: 0 },
    });
    render(<IncidentsPage />);
    await waitFor(() => expect(api.listIncidents).toHaveBeenCalledWith("open"));

    fireEvent.click(screen.getByRole("button", { name: /^All/i }));
    await waitFor(() => expect(api.listIncidents).toHaveBeenCalledWith(undefined));
  });
});

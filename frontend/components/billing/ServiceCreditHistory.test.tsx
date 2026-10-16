import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet } from "../../lib/api";
import type { ServiceCreditHistoryPage, ServiceCreditOperation } from "../../lib/serviceCreditHistory";
import { ServiceCreditHistory } from "./ServiceCreditHistory";

vi.mock("../../lib/api", () => ({ apiGet: vi.fn() }));
const getHistory = vi.mocked(apiGet);

const search: ServiceCreditOperation = {
  id: "resv_search", operation: "job_search", source_id: "search_accepted", unit_price: 2,
  requested_count: 5, reserved_amount: 10, held_amount: 0, committed_amount: 6,
  released_amount: 4, state: "settled", pricing_version: "price-accepted-v1",
  created_at: "2026-10-14T10:00:00Z", settled_at: "2026-10-14T10:01:00Z",
};
const application: ServiceCreditOperation = {
  ...search, id: "resv_application", operation: "job_application", source_id: "application_unknown",
  requested_count: 1, unit_price: 20, reserved_amount: 20, held_amount: 20,
  committed_amount: 0, released_amount: 0, state: "reserved", settled_at: null,
};

function page(operations: ServiceCreditOperation[], nextCursor: string | null = null): ServiceCreditHistoryPage {
  return { balance: 80, reserved_credits: 20, reservations: [application], items: [], operations, next_cursor: nextCursor };
}

describe("ServiceCreditHistory", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows settled search costs and unknown application holds as separate operations", async () => {
    getHistory.mockResolvedValueOnce(page([search, application]));
    render(<ServiceCreditHistory refreshKey="" />);
    await screen.findByRole("heading", { name: "Employer search" });
    const searchRow = screen.getByRole("heading", { name: "Employer search" }).closest("li")!;
    expect(within(searchRow).getByText("2 credits per job · 5 requested")).toBeInTheDocument();
    expect(within(searchRow).getByText("Price version: price-accepted-v1")).toBeInTheDocument();
    expect(within(searchRow).getByText("Settled · unused credits released")).toBeInTheDocument();
    expect(within(searchRow).getByText("Charged").nextElementSibling).toHaveTextContent("6credits");
    expect(within(searchRow).getByText("Released").nextElementSibling).toHaveTextContent("4credits");
    const applicationRow = screen.getByRole("heading", { name: "Automatic application" }).closest("li")!;
    expect(within(applicationRow).getByText("Credits held")).toBeInTheDocument();
    expect(within(applicationRow).getByText("Held now").nextElementSibling).toHaveTextContent("20credits");
    expect(within(applicationRow).getByText("Charged").nextElementSibling).toHaveTextContent("0credits");
  });

  it("requests older entries with the server cursor and filters applications without mixing results", async () => {
    getHistory.mockResolvedValueOnce(page([search], "resv_search"));
    getHistory.mockResolvedValueOnce(page([{ ...search, id: "resv_older", source_id: "search_older" }]));
    getHistory.mockResolvedValueOnce(page([application]));
    render(<ServiceCreditHistory refreshKey="" />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older activity" }));
    await waitFor(() => expect(screen.getAllByRole("heading", { name: "Employer search" })).toHaveLength(2));
    expect(getHistory.mock.calls[1][0]).toBe("/v1/employer-jobs/credits?limit=10&cursor=resv_search");
    fireEvent.click(screen.getByRole("button", { name: "Applications" }));
    await screen.findByRole("heading", { name: "Automatic application" });
    expect(screen.queryByRole("heading", { name: "Employer search" })).not.toBeInTheDocument();
    expect(getHistory.mock.calls[2][0]).toBe("/v1/employer-jobs/credits?limit=10&operation=job_application");
  });

  it("aborts a previous filter request so late data cannot replace the active filter", async () => {
    let resolveOld: (value: ServiceCreditHistoryPage) => void = () => undefined;
    getHistory.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    getHistory.mockResolvedValueOnce(page([application]));
    render(<ServiceCreditHistory refreshKey="" />);
    fireEvent.click(screen.getByRole("button", { name: "Applications" }));
    await screen.findByRole("heading", { name: "Automatic application" });
    expect(getHistory.mock.calls[0][1]?.aborted).toBe(true);
    await act(async () => resolveOld(page([search])));
    expect(screen.queryByRole("heading", { name: "Employer search" })).not.toBeInTheDocument();
  });

  it("shows unavailable history as an error and permits a read-only retry", async () => {
    getHistory.mockRejectedValueOnce(new Error("History unavailable"));
    getHistory.mockResolvedValueOnce(page([]));
    render(<ServiceCreditHistory refreshKey="" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("History unavailable");
    expect(screen.queryByText("No service-credit activity yet.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));
    await screen.findByText("No service-credit activity yet.");
    expect(getHistory).toHaveBeenCalledTimes(2);
  });
});

import type { components } from "./generated/api";

export type ServiceCreditOperation = components["schemas"]["ServiceCreditOperation"];
export type ServiceCreditHistoryPage = components["schemas"]["ServiceCreditHistory"];
export type ServiceOperationFilter = "all" | "job_search" | "job_application";

export function serviceCreditHistoryPath(operation: ServiceOperationFilter, cursor?: string | null): string {
  const query = new URLSearchParams({ limit: "10" });
  if (operation !== "all") query.set("operation", operation);
  if (cursor) query.set("cursor", cursor);
  return `/v1/employer-jobs/credits?${query.toString()}`;
}

export function serviceOperationLabel(operation: string): string {
  if (operation === "job_search") return "Employer search";
  if (operation === "job_application") return "Automatic application";
  return operation.replaceAll("_", " ");
}

export function serviceOperationStatus(row: ServiceCreditOperation): string {
  if (row.state === "reserved") return row.held_amount > 0 ? "Credits held" : "Reservation pending";
  if (row.state === "settled") {
    if (row.committed_amount === 0 && row.released_amount > 0) return "Released";
    return row.released_amount > 0 ? "Settled · unused credits released" : "Settled";
  }
  return row.state.replaceAll("_", " ");
}

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { AnalysisRun } from "../lib/career";
import { AnalysisRunFeedback } from "./AnalysisRunFeedback";

const base: AnalysisRun = {
  id: "synthetic_failed_run", opportunity_id: "synthetic_opportunity", operation: "resume_tailor",
  status: "failed", estimated_units: 2, committed_units: 0, usage_state: "released",
  provider: null, model: null, prompt_version: null, error_code: "TailoringOutputError",
  attempt_count: 1, generation_attempt_limit: 3, generation_attempt_count: 2,
  cancel_requested: false, created_at: "2026-10-11T00:00:00Z", updated_at: "2026-10-11T00:00:01Z",
  started_at: "2026-10-11T00:00:00Z", completed_at: "2026-10-11T00:00:01Z", cancelled_at: null,
  input_purged_at: null, result_purged_at: null,
};
afterEach(() => cleanup());

it("shows the actual format refusal with useful alternatives and only explicit snapshot action", () => {
  const snapshot = vi.fn();
  const request = vi.spyOn(globalThis, "fetch");
  render(<AnalysisRunFeedback run={{ ...base, failure_guidance: { reason_code: "replacement_does_not_fit", next_action: "shorten_wording" } }} onSaveSourceSnapshot={snapshot} />);
  expect(screen.getByRole("alert")).toHaveTextContent("The proposed wording does not fit the original layout.");
  expect(screen.getByRole("alert")).toHaveTextContent("Use shorter wording in an editable source");
  expect(screen.getByRole("alert")).toHaveTextContent("Any reserved units were released.");
  expect(screen.getByRole("alert")).toHaveTextContent("Your original resume is unchanged.");
  expect(screen.queryByText(/please try again/i)).toBeNull();
  expect(screen.getByRole("link", { name: "Choose original or custom resume" })).toHaveAttribute("href", "/resume");
  expect(snapshot).not.toHaveBeenCalled();
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save unchanged source snapshot" }));
  expect(snapshot).toHaveBeenCalledTimes(1);
  expect(request).not.toHaveBeenCalled();
  request.mockRestore();
});

it("keeps source snapshot disabled while unavailable or being saved", () => {
  const snapshot = vi.fn();
  render(<AnalysisRunFeedback run={base} onSaveSourceSnapshot={snapshot} sourceSnapshotDisabled />);
  const button = screen.getByRole("button", { name: "Save unchanged source snapshot" });
  expect(button).toBeDisabled(); fireEvent.click(button); expect(snapshot).not.toHaveBeenCalled();
});

it.each([undefined, null, { reason_code: "/private/customer.docx <script>" }, { reason_code: "__proto__" }])("legacy/unknown guidance stays static and never reflects server text: %j", (failure_guidance) => {
  render(<AnalysisRunFeedback run={{ ...base, failure_guidance } as AnalysisRun} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Could not verify safe, useful changes for this role.");
  expect(screen.getByRole("alert")).not.toHaveTextContent("/private");
  expect(screen.getByRole("alert")).not.toHaveTextContent("<script>");
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByRole("link")).toHaveAttribute("href", "/resume");
});

it.each([
  ["font_unsupported", "use_supported_font", "characters the original font cannot preserve"],
  ["source_links_protected", "keep_link_text", "alter an existing resume link"],
  ["tex_worker_unavailable", "use_original_or_custom", "isolated LaTeX compiler is unavailable"],
  ["no_supported_changes", "use_original_or_custom", "No safe, useful changes were found"],
])("shows a distinct static next step for %s", (reason_code, next_action, title) => {
  render(<AnalysisRunFeedback run={{ ...base, failure_guidance: { reason_code, next_action } } as AnalysisRun} />);
  expect(screen.getByRole("alert")).toHaveTextContent(title);
  expect(screen.queryByText(/please try again/i)).toBeNull();
});

it("does not claim released units while an expense hold is unresolved", () => {
  render(<AnalysisRunFeedback run={{ ...base, usage_state: "reserved" }} />);
  expect(screen.getByRole("alert")).not.toHaveTextContent("were released");
  expect(screen.getByRole("alert")).not.toHaveTextContent("No analysis units were charged");
});

it("keeps succeeded, cancelled and pending operations out of the failure actions", () => {
  const snapshot = vi.fn();
  const view = render(<AnalysisRunFeedback run={{ ...base, status: "succeeded" }} onSaveSourceSnapshot={snapshot} />);
  expect(screen.queryByRole("alert")).toBeNull(); expect(screen.queryByRole("link")).toBeNull();
  view.rerender(<AnalysisRunFeedback run={{ ...base, status: "queued" }} onSaveSourceSnapshot={snapshot} />);
  expect(screen.getByRole("status")).toHaveTextContent("Analysis is queued"); expect(screen.queryByRole("button")).toBeNull();
  view.rerender(<AnalysisRunFeedback run={{ ...base, status: "cancelled" }} onSaveSourceSnapshot={snapshot} />);
  expect(screen.getByText(/Analysis cancelled/)).toBeVisible(); expect(screen.queryByRole("button")).toBeNull();
  expect(snapshot).not.toHaveBeenCalled();
});

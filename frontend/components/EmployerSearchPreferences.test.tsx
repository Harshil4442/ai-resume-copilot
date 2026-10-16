import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmployerSearchPreferences } from "./EmployerSearchPreferences";
import { emptyPreferenceDraft, preferenceDraft, preferenceInput } from "../lib/searchPreferences";

afterEach(cleanup);
describe("explicit employer search preferences", () => {
  it("keeps unanswered choices null and includes unknown facts", () => {
    const result = preferenceInput(emptyPreferenceDraft);
    expect(result.error).toBeNull();
    expect(result.preferences).toMatchObject({ version: 1, salary: null, country_codes: null, sponsorship_required: null, willing_to_relocate: null, unknown_metadata: "include" });
    expect(preferenceDraft(undefined)).toEqual(emptyPreferenceDraft);
  });
  it("retains explicit choices and decimal salary through saved-search restoration", () => {
    const draft = { ...emptyPreferenceDraft, countries: "IN, US", languages: "en, pt-BR", employment: ["full_time" as const], minimum: "123.45", maximum: "200", currency: "USD", sponsorship: "yes" as const, relocation: "no" as const, authorizedCountries: "IN" };
    const result = preferenceInput(draft);
    expect(result.error).toBeNull();
    expect(preferenceDraft(result.preferences)).toEqual(draft);
    expect(result.preferences?.sponsorship_required).toBe(true);
  });
  it("preserves explicit no-authorization separately from an unanswered choice", () => {
    const result = preferenceInput({ ...emptyPreferenceDraft, noAuthorization: true });
    expect(result.preferences?.authorized_country_codes).toEqual([]);
    expect(preferenceDraft(result.preferences).noAuthorization).toBe(true);
  });
  it.each([
    { countries: "India" }, { countries: "IN, IN" }, { languages: "English" },
    { minimum: "NaN" }, { minimum: "1.234" }, { minimum: "200", maximum: "100" }, { minimum: "10", currency: "Rupees" },
  ])("rejects ambiguous input before a paid request: %j", (changes) => {
    expect(preferenceInput({ ...emptyPreferenceDraft, ...changes }).error).not.toBeNull();
  });
  it("exposes distinct accessible controls and explicit unknown help without inferred defaults", () => {
    const update = vi.fn();
    render(<EmployerSearchPreferences value={emptyPreferenceDraft} onChange={update} disabled={false} error={null} />);
    expect(screen.getByText(/Jobs with missing employer facts stay in results/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Country codes/), { target: { value: "IN" } });
    expect(update).toHaveBeenLastCalledWith({ ...emptyPreferenceDraft, countries: "IN" });
    fireEvent.change(screen.getByLabelText("Do you need sponsorship?"), { target: { value: "yes" } });
    expect(update).toHaveBeenLastCalledWith({ ...emptyPreferenceDraft, sponsorship: "yes" });
    expect(screen.getByText(/This does not establish working-language requirements/)).toBeInTheDocument();
  });
  it("disables changes during a paid request and presents validation errors", () => {
    render(<EmployerSearchPreferences value={emptyPreferenceDraft} onChange={vi.fn()} disabled error="Salary minimum cannot exceed maximum." />);
    expect(screen.getByLabelText(/Country codes/)).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Salary minimum cannot exceed maximum.");
  });
});

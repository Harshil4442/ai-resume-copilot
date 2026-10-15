export type EmploymentType = "full_time" | "part_time" | "contract" | "internship" | "temporary" | "freelance" | "permanent" | "traineeship";
export type PayPeriod = "year" | "month" | "week" | "day" | "hour";
export type SearchPreferencesV1 = {
  version: 1; country_codes: string[] | null; posting_languages: string[] | null;
  employment_types: EmploymentType[] | null;
  salary: { minimum: string | null; maximum: string | null; currency: string; period: PayPeriod } | null;
  sponsorship_required: boolean | null; authorized_country_codes: string[] | null;
  willing_to_relocate: boolean | null; unknown_metadata: "include";
};
export type PreferenceDraft = {
  countries: string; languages: string; employment: EmploymentType[];
  minimum: string; maximum: string; currency: string; period: PayPeriod;
  sponsorship: "" | "yes" | "no"; authorizedCountries: string; noAuthorization: boolean; relocation: "" | "yes" | "no";
};
export const emptyPreferenceDraft: PreferenceDraft = {
  countries: "", languages: "", employment: [], minimum: "", maximum: "", currency: "INR", period: "year",
  sponsorship: "", authorizedCountries: "", noAuthorization: false, relocation: "",
};
export const employmentOptions: { value: EmploymentType; label: string }[] = [
  { value: "full_time", label: "Full-time" }, { value: "part_time", label: "Part-time" },
  { value: "contract", label: "Contract" }, { value: "internship", label: "Internship" },
  { value: "temporary", label: "Temporary" }, { value: "freelance", label: "Freelance" },
  { value: "permanent", label: "Permanent" }, { value: "traineeship", label: "Traineeship" },
];
function choices(raw: string, pattern: RegExp, normalize: (value: string) => string): string[] | null {
  if (!raw.trim()) return null;
  const values = raw.split(",").map((value) => normalize(value.trim()));
  if (values.length > 20 || values.some((value) => !pattern.test(value)) || new Set(values).size !== values.length) throw new Error("Enter unique comma-separated codes (maximum 20).");
  return values;
}
function money(raw: string): string | null {
  if (!raw.trim()) return null;
  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(raw.trim())) throw new Error("Salary must be a nonnegative amount with at most two decimal places.");
  return raw.trim();
}
function cents(value: string): bigint {
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
}
export function preferenceInput(draft: PreferenceDraft): { preferences: SearchPreferencesV1 | null; error: string | null } {
  try {
    const countryCodes = choices(draft.countries, /^[A-Z]{2}$/, (value) => value.toUpperCase());
    const languages = choices(draft.languages, /^[a-z]{2,3}(?:-[A-Z]{2})?$/, (value) => value);
    const authorized = draft.noAuthorization ? [] : choices(draft.authorizedCountries, /^[A-Z]{2}$/, (value) => value.toUpperCase());
    const minimum = money(draft.minimum), maximum = money(draft.maximum);
    if (minimum !== null && maximum !== null && cents(minimum) > cents(maximum)) throw new Error("Salary minimum cannot exceed maximum.");
    if ((minimum !== null || maximum !== null) && !/^[A-Z]{3}$/.test(draft.currency.trim().toUpperCase())) throw new Error("Enter a three-letter salary currency code, such as INR or USD.");
    return { error: null, preferences: {
      version: 1, country_codes: countryCodes, posting_languages: languages,
      employment_types: draft.employment.length ? draft.employment : null,
      salary: minimum !== null || maximum !== null ? { minimum, maximum, currency: draft.currency.trim().toUpperCase(), period: draft.period } : null,
      sponsorship_required: draft.sponsorship ? draft.sponsorship === "yes" : null,
      authorized_country_codes: authorized, willing_to_relocate: draft.relocation ? draft.relocation === "yes" : null, unknown_metadata: "include",
    } };
  } catch (error) { return { preferences: null, error: error instanceof Error ? error.message : "Check your search preferences." }; }
}
export function preferenceDraft(preferences: SearchPreferencesV1 | null | undefined): PreferenceDraft {
  if (!preferences) return { ...emptyPreferenceDraft, employment: [] };
  return {
    countries: preferences.country_codes?.join(", ") ?? "", languages: preferences.posting_languages?.join(", ") ?? "",
    employment: preferences.employment_types ?? [], minimum: preferences.salary?.minimum ?? "", maximum: preferences.salary?.maximum ?? "",
    currency: preferences.salary?.currency ?? "INR", period: preferences.salary?.period ?? "year",
    sponsorship: preferences.sponsorship_required === null ? "" : preferences.sponsorship_required ? "yes" : "no",
    authorizedCountries: preferences.authorized_country_codes?.join(", ") ?? "", noAuthorization: preferences.authorized_country_codes?.length === 0,
    relocation: preferences.willing_to_relocate === null ? "" : preferences.willing_to_relocate ? "yes" : "no",
  };
}

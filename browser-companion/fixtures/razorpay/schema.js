// Hand-authored public-shape reference. Never a production employer adapter.
export const FIXTURE_PATH = "/fixture/razorpay-like/apply";
export const FIXTURE_MARKER = "synthetic-razorpay-like-v1";
export const SYNTHETIC_IDENTITY = Object.freeze({ tenant: "synthetic_razorpay_like", employer: "synthetic_employer", opening: "synthetic_opening" });
export const ASSISTED_FIELDS = Object.freeze([
  ["first_name", "First Name"], ["last_name", "Last Name"], ["email", "Email"],
  ["question_8970454005", "LinkedIn Profile"], ["question_8970455005", "Website"],
  ["question_8970456005", "Total Years of Experience"], ["question_8970461005", "Current Location"],
]);
export const CHALLENGES = Object.freeze(["login", "captcha", "security-code", "mfa", "account-switch", "autofill", "google-drive", "dropbox"]);
export const RECIPIENT_POLICY = Object.freeze({
  version: "synthetic-recipient-policy-v1",
  fill: [{ field: "email", event: "blur", observed_recipient: "https://email-address-validator.us.greenhouse.io/address/validate", simulation: "/fixture/razorpay-like/email-validator", disclosure: "Email can be sent for validation before final submission." }],
  manual: [{ field: "resume", event: "file-selection", observed_recipient: "storage endpoint unresolved in public review", simulation: "/fixture/razorpay-like/manual-file", disclosure: "Manual attachment immediately discloses file bytes to a local recorder in this fixture." }],
  limitations: "Declared policy is test input, not verification of live JavaScript, recipients or safety. Cancelling cannot recall callbacks.",
});
const industryLabels = ["Not Applicable", "Commercial Real Estate", "Computer and Network Security", "Computer Software", "Computer Hardware", "Internet", "Civil Engineering", "Public Policy", "Public Relations and Communication", "Customer Service / Care", "Information Services", "Information and Technology Services", "International Trade and Development", "Investment Banking", "Banking", "Legal Services", "Facilities Services", "Travel and Tourism", "Human Resources", "Consulting", "Accounting", "Financial Services", "Executive Office", "Marketing and Advertising", "Media and Branding", "Professional Training and Coaching", "FMCG"];
const stage = ["Experienced Professional", "College Grads / Fresher", "Student / College Intern"];
export const CHOICES = Object.freeze({
  country: [{ value: "synthetic-IN", label: "India (synthetic subset)" }, { value: "synthetic-MY", label: "Malaysia (synthetic subset)" }],
  question_8970457005: stage.map((label, index) => ({ label, value: String(23857236005 + index * 1000) })),
  question_8970458005: ["Male", "Female", "Others"].map((label, index) => ({ label, value: String(23857239005 + index * 1000) })),
  question_8970459005: industryLabels.map((label, index) => ({ label, value: String(23857242005 + index * 1000) })),
  question_8970460005: stage.map((label, index) => ({ label, value: String(23857269005 + index * 1000) })),
});
export function validateAnswers(answers) {
  if (!answers || Array.isArray(answers) || typeof answers !== "object") throw new Error("Exact synthetic answer map is required");
  const keys = Object.keys(answers);
  const allowed = new Set(ASSISTED_FIELDS.map(([id]) => id));
  if (!keys.length || keys.length > 8 || keys.some((key) => !allowed.has(key))) throw new Error("Only the seven optional assisted text fields are supported; all other controls are manual");
  for (const key of keys) if (typeof answers[key] !== "string" || !answers[key].trim() || answers[key].length > 255) throw new Error("Assisted values must be explicit nonempty strings of at most 255 characters");
  return keys;
}

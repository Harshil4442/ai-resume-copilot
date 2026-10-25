import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { signIn } from "next-auth/react";
import { accountMutationHeaders } from "../lib/accountTransportClient";
import { apiDownload } from "../lib/api";
import BrowserAssistanceReview from "./BrowserAssistanceReview";
import { isHostedReceiptReview } from "../lib/browserHostedNetworkReview";
import countryOptions from "../../backend/app/domains/browser_assistance/hosted_country_options.json";

vi.mock("next-auth/react", () => ({ signIn: vi.fn() }));
vi.mock("../lib/accountTransportClient", () => ({ accountMutationHeaders: vi.fn() }));
vi.mock("../lib/api", () => ({ apiDownload: vi.fn() }));
const identity = "00000000-0000-4000-8000-000000000001";
const application = "00000000-0000-4000-8000-000000000002";
const target = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005";
const request = vi.fn();
function device(state = "pending") { return { id: identity, state, key_sha256: "a".repeat(64), extension_id: "fixture-extension", expires_at_ms: Date.now() + 60000 }; }
function review(state = "draft") {
  return { id: identity, state, expires_at_ms: Date.now() + 60000, review_digest: "b".repeat(64), filled_count: 0,
    payload: { application_id: application, target_url: target, package_digest: "c".repeat(64),
      artifact: { filename: "Synthetic exact resume.pdf", media_type: "application/pdf", size_bytes: 1024, sha256: "d".repeat(64) },
      context_digest: "e".repeat(64), reviewed_answers_sha256: "f".repeat(64),
      fields: [{ id: "first_name", label: "First Name", type: "text", value: "Synthetic Alice", expected_before: "" }, { id: "email", label: "Email", type: "text", value: "synthetic@example.test", expected_before: "" }],
      manual_fields: [{ id: "phone", label: "Phone", required: true }, { id: "resume", label: "Resume/CV", required: true }, { id: "question_8970458005", label: "Gender", required: true }],
      disclosures: ["Email blur can send the address to Greenhouse validation before final submission."], allowed_actions: ["fill"], fee_credits: 0 } };
}
function typedReview(state = "draft") {
  const result = review(state);
  return { ...result, payload: { ...result.payload, review_protocol_version: 2,
    fields: [...result.payload.fields,
      { id: "phone", label: "Phone", type: "tel", value: "+919000000000", expected_before: "" },
      { id: "question_9132967005", label: "Current Career Stage", type: "combobox", value: "owned-choice", expected_before: "",
        options: [{ value: "owned-choice", label: "Explicit candidate choice" }, { value: "other-choice", label: "Other candidate choice" }],
        value_label: "Explicit candidate choice", expected_before_label: "" }],
    manual_fields: [{ id: "country", label: "Country", required: true }],
    disclosures: [...result.payload.disclosures, "Selecting this exact approved dropdown option can autosave before submission."] } };
}
function attachmentReview(state = "draft") {
  const original = review(state);
  return { ...original, filled_count: state === "filled" ? 1 : 0, review_protocol_version: 3,
    attachment_status: state === "filled" ? "selected_upload_unconfirmed" : "unknown",
    payload: { ...original.payload, review_protocol_version: 3, fields: [] as typeof original.payload.fields,
      reviewed_answers: {} as Record<string, string>, allowed_actions: ["attach"],
      artifact: { ...original.payload.artifact, artifact_id: "artifact_owned", resume_id: 7, resume_version_id: null as string | null, resume_choice: "original" },
      attachment: { input_id: "resume", expected_empty: true },
      manual_fields: [{ id: "country", label: "Country", required: true }],
      disclosures: ["Selecting the file can upload it before final submission; the storage recipient is not verified."] } };
}
type HostedControl = { id: string; label: string; type: string; required: boolean; options?: { value: string; label: string }[] };
type HostedField = { id: string; label: string; type: string; value: string; expected_before: string;
  required?: boolean; options?: { value: string; label: string }[]; value_label?: string; expected_before_label?: string };
const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"].map((value) => ({ value, label: value }));
function finalSubmitReview(state = "draft") {
  const base = attachmentReview(state);
  const answers = [
    { id: "first_name", label: "First Name", type: "text", required: true, value: "Synthetic Alice" },
    { id: "last_name", label: "Last Name", type: "text", required: true, value: "Fixture" },
    { id: "email", label: "Email", type: "text", required: true, value: "synthetic@example.test" },
    { id: "question_9132967005", label: "Current Career Stage", type: "combobox", required: true, value: "owned-choice", options: [{ value: "owned-choice", label: "Candidate selected career stage" }] },
  ];
  return { ...base, review_protocol_version: 4, filled_count: 0, submission_status: state === "unknown" ? "unknown" : "not_launched", employer_confirmed: false, fee_credits: 20,
    payload: { ...base.payload, review_protocol_version: 4, allowed_actions: ["submit"], fee_credits: 20,
      target_url: "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4740333005", candidate_id: 17, company: "Razorpay",
      job: { posting_id: "owned_posting", external_id: "4740333005", title: "Lead Software Development Engineer", content_sha256: "a".repeat(64) },
      document_id: "owned-chrome-document", frame_id: 0, field_mapping: answers.map(({ id, label, type, required, options }) => ({ id, label, type, required, ...options ? { options } : {} })), answers,
      reviewed_answers: Object.fromEntries(answers.map(({ id, value }) => [id, value])), assisted_values: Object.fromEntries(answers.map(({ id, value }) => [id, value])),
      manual_fields: [] as { id: string; label: string; required: boolean }[], upload: { review_id: identity, action_id: application, filename: base.payload.artifact.filename, status: "portal_reports_uploaded" },
      upload_continuity: { review_id: identity, action_id: application, artifact_sha256: base.payload.artifact.sha256, document_id: "owned-chrome-document", target_url: "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4740333005" },
      submit_control: { form_id: "application-form", type: "submit", label: "Submit application" }, pricing: { unit_price: 20 }, admission: { policy_revision: "owned_policy" },
      disclosures: ["Reserve 20 credits for this exact final submission. Credits remain held while the employer outcome is unknown; settlement requires a verified employer receipt.",
        "One final Submit application click can immediately disclose every reviewed answer and resume. Cancellation cannot recall an authorized click; unknown outcomes are never resent automatically."] } };
}
function nativeNetwork(url: string, upload = false) {
  return { request_id: "owned-native-network", method: upload ? "POST" : "GET", url, tab_id: -1, frame_id: -1, document_id: null,
    initiator: "chrome-extension://pnjmegppacleipajnifjmbcopjjffklb", started_at_ms: Date.now() - 1000, completed_at_ms: Date.now() - 500,
    browser_major: 151, status_code: 200, https_validation: "browser_default", request_body_source: upload ? "extension_owned_arraybuffer" : "none",
    native_body_crosscheck: upload ? "raw" : "none", request_body_sha256: upload ? "8".repeat(64) : null,
    request_body_size: upload ? 2048 : 0, from_cache: false, redirected: false, ip: "198.51.100.8" };
}
function nativeUploadReview(state = "draft") {
  const base = attachmentReview(state);
  return { ...base, attachment_status: state === "filled" ? "uploaded_native_verified" : "unknown",
    payload: { ...base.payload, allowed_actions: ["upload_resume"], document_id: "owned-chrome-document", frame_id: 0,
      native_context: { tab_id: 44, time_zone: "Asia/Kolkata" }, upload_plan: {
        body_provenance: "extension_owned_arraybuffer", transport: "greenhouse-owned-upload-browser-https-v2", presign_url: "https://boards.greenhouse.io/uncacheable_attributes/presigned_fields?fields%5B%5D=resume",
        storage_origin: "https://owned-resume.s3.amazonaws.com", storage_key: "owned/exact resume.pdf", policy_sha256: "1".repeat(64), fields_sha256: "2".repeat(64),
        presign_response_sha256: "3".repeat(64), resume_component_sha256: "4".repeat(64), expires_at_ms: Date.now() + 60000,
        presign_evidence: nativeNetwork("https://boards.greenhouse.io/uncacheable_attributes/presigned_fields?fields%5B%5D=resume") },
      disclosures: ["This upload approval does not submit the application or charge application credits. Final submission needs a separate complete review."] } };
}
function nativeFinalReview(state = "draft") {
  const base = finalSubmitReview(state), artifact = base.payload.artifact;
  const native = { version: "greenhouse_owned_upload_browser_https_v2", review_id: identity, action_id: application, review_digest: "9".repeat(64),
    artifact_sha256: artifact.sha256, artifact_size_bytes: artifact.size_bytes, filename: artifact.filename, target_url: base.payload.target_url,
    document_id: base.payload.document_id, tab_id: 44, uploaded_url: "https://owned-resume.s3.amazonaws.com/owned/exact%20resume.pdf",
    multipart_sha256: "8".repeat(64), multipart_size: 2048, native: nativeNetwork("https://owned-resume.s3.amazonaws.com/", true) };
  return { ...base, payload: { ...base.payload, receipt_contract: "greenhouse_hosted_browser_https_200_v2", native_context: { tab_id: 44, time_zone: "Asia/Kolkata" },
    upload: { ...base.payload.upload, native_upload: native, upload_plan_sha256: "5".repeat(64) }, expected_application_sha256: "6".repeat(64),
    expected_application: { first_name: "Synthetic Alice", last_name: "Fixture", email: "synthetic@example.test", answers_attributes: {
      "9132967005": { question_id: "9132967005", priority: 0, answer_selected_options_attributes: { "0": { question_option_id: "owned-choice" } } } },
      demographic_answers: [], data_compliance: {}, attachments: {}, from_job_board_renderer: true, employments: [], mapped_url_token: null,
      appcast_click_id: null, time_zone: "Asia/Kolkata", resume_url: native.uploaded_url, resume_url_filename: artifact.filename } } };
}
function hostedReview(rows = [{ index: 0, current_role: false }], withBaseActions = false) {
  const base = typedReview();
  const controls: HostedControl[] = [{ id: "country", label: "Country", type: "combobox", required: true, options: structuredClone(countryOptions) }];
  for (const { index, current_role } of rows) {
    const parts = [["company-name", "Company name", "text"], ["title", "Title", "text"], ["start-date-month", "Start date month", "combobox"], ["start-date-year", "Start date year", "text"],
      ...current_role ? [] : [["end-date-month", "End date month", "combobox"], ["end-date-year", "End date year", "text"]], ["current-role", "Current role", "checkbox"]];
    for (const [name, label, type] of parts) controls.push({ id: `${name}-${index}${type === "checkbox" ? "_1" : ""}`, label, type, required: type !== "checkbox", ...(type === "combobox" ? { options: months } : {}) });
  }
  if (rows.length < 5) controls.push({ id: `employment-add-${rows.length}`, label: "Add another", type: "button", required: false });
  const values: Record<string, string> = Object.fromEntries(controls.map(({ id, type }) => [id, type === "button" ? String(rows.length) : type === "checkbox" ? String(rows[Number(id.split("-").at(-1)!.split("_")[0])].current_role) : id === "country" ? "us" : ""]));
  return { ...base, review_protocol_version: 2, payload: { ...base.payload, fields: (withBaseActions ? base.payload.fields : []) as HostedField[],
    document_id: "owned-chrome-document", frame_id: 0, field_mapping: [
      { id: "first_name", label: "First Name", type: "text", required: true }, { id: "last_name", label: "Last Name", type: "text", required: true },
      { id: "email", label: "Email", type: "text", required: true }, ...controls],
    manual_fields: [...base.payload.manual_fields.filter(({ id }) => !controls.some((control) => control.id === id)),
      ...controls.filter(({ id, required }) => required && !values[id]).map(({ id, label, required }) => ({ id, label, required }))],
    reviewed_answers: Object.fromEntries((withBaseActions ? base.payload.fields : []).map(({ id, value }) => [id, value])) as Record<string, string>, hosted: { rows, controls, values, draft_answers: {} as Record<string, string> } } };
}
function revisedHostedReview(base: ReturnType<typeof hostedReview>, answers: Record<string, string>) {
  const result = structuredClone(base);
  result.review_digest = "1".repeat(64); result.payload.reviewed_answers_sha256 = "2".repeat(64); result.payload.hosted.draft_answers = answers;
  const { controls, values } = result.payload.hosted;
  result.payload.fields = [...result.payload.fields.filter(({ id }) => !controls.some((control) => control.id === id)),
    ...controls.filter(({ id }) => Object.hasOwn(answers, id) && answers[id] !== values[id]).map((control) => ({ ...control, value: answers[control.id], expected_before: values[control.id],
      ...control.type === "combobox" ? { value_label: control.options!.find(({ value }) => value === answers[control.id])!.label, expected_before_label: control.options!.find(({ value }) => value === values[control.id])?.label ?? "" } : {} }))];
  result.payload.reviewed_answers = Object.fromEntries(result.payload.fields.map(({ id, value }) => [id, value]));
  result.payload.manual_fields = [...result.payload.manual_fields.filter(({ id }) => !controls.some((control) => control.id === id)),
    ...controls.filter(({ id, required }) => required && !values[id] && !Object.hasOwn(answers, id)).map(({ id, label, required }) => ({ id, label, required }))];
  return result;
}
beforeEach(() => { vi.resetAllMocks(); vi.stubGlobal("fetch", request); vi.mocked(accountMutationHeaders).mockResolvedValue({ "Content-Type": "application/json", "X-Hirewiz-Account-CSRF": "9".repeat(64) }); vi.mocked(apiDownload).mockResolvedValue(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
async function loadReview() {
  render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
  await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
  await screen.findByRole("button", { name: "Approve reviewed text assistance" });
}
async function acceptReview() {
  await userEvent.click(screen.getByRole("button", { name: "Download exact review file" }));
  await userEvent.click(screen.getByRole("checkbox"));
}

it("offers the distinct recovery package and preserves an existing install without any request", () => {
  render(<BrowserAssistanceReview deviceId={null} reviewId={null} applicationId={null} />);
  expect(screen.getByRole("link", { name: "Download HireWiz companion 0.8.3" })).toHaveAttribute("href", "/downloads/hirewiz-browser-companion-0.8.3.zip");
  expect(screen.getByText(/replace its files and reload that same extension; keep its browser storage/)).toBeInTheDocument();
  expect(request).not.toHaveBeenCalled();
});

describe("separate exact final-submit approval", () => {
  const approve = () => screen.getByRole("button", { name: "Approve final submission · reserve 20 credits" });
  const consent = () => screen.getByRole("checkbox", { name: /I reviewed every answer, candidate, opening/ });
  async function load(result = finalSubmitReview()) {
    request.mockResolvedValueOnce(Response.json(result));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={application} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    await screen.findByRole("heading", { name: "Review final submission" });
  }

  it("shows the candidate, employer, opening, current answers and exact resume source before an independent 20-credit approval", async () => {
    const draft = finalSubmitReview();
    await load(draft);
    expect(approve()).toBeDisabled(); expect(consent()).not.toBeChecked(); expect(consent()).toBeEnabled();
    expect(screen.getByText(/Candidate #17 · Razorpay · Lead Software Development Engineer/)).toBeVisible();
    expect(screen.getByText(/Opening 4740333005/)).toBeVisible();
    expect(screen.getByRole("link", { name: draft.payload.target_url })).toHaveAttribute("href", draft.payload.target_url);
    for (const label of ["First Name", "Last Name", "Email", "Current Career Stage"]) expect(screen.getByText(label, { selector: "dt" })).toBeVisible();
    for (const answer of ["Synthetic Alice", "Fixture", "synthetic@example.test", "Candidate selected career stage"]) expect(screen.getByText(answer)).toBeVisible();
    expect(screen.getByText(/Synthetic exact resume.pdf · original resume #7/)).toBeVisible();
    expect(screen.getByText(`SHA-256: ${"d".repeat(64)}`)).toBeVisible();
    expect(screen.getByText(/settlement requires a verified employer receipt/)).toBeVisible();
    expect(screen.getByText(/This is browser-observed upload state/)).toBeVisible();
    expect(screen.queryByText(/0 application credits/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Download exact review file" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve exact attachment assistance" })).toBeNull();
    expect(apiDownload).not.toHaveBeenCalled(); expect(request).toHaveBeenCalledTimes(1);
    request.mockResolvedValueOnce(Response.json({ ...draft, state: "approved" }));
    await userEvent.click(consent()); expect(approve()).toBeEnabled(); await userEvent.click(approve());
    expect(await screen.findByText(/Final submission approved and 20 credits reserved/)).toBeVisible();
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true, review_digest: draft.review_digest });
    expect(request.mock.calls[1][1].headers["X-Hirewiz-Account-CSRF"]).toBe("9".repeat(64));
    expect(request.mock.calls.map(([url]) => url)).toEqual(["/api/browser-assistance/candidate/review", "/api/browser-assistance/candidate/approve"]);
    expect(screen.queryByRole("checkbox")).toBeNull(); expect(screen.queryByText(/0 application credits/)).toBeNull();
  });

  it.each(["fee", "pricing", "action", "upload-name", "upload-status", "upload-review", "upload-continuity", "answer", "assisted", "mapping", "required", "source", "candidate", "opening", "opening-destination"])("rejects tampered %s before presenting final-submit consent", async (change) => {
    const result = finalSubmitReview();
    if (change === "fee") result.payload.fee_credits = 0;
    if (change === "pricing") result.payload.pricing.unit_price = 0;
    if (change === "action") result.payload.allowed_actions = ["fill"];
    if (change === "upload-name") result.payload.upload.filename = "Another resume.pdf";
    if (change === "upload-status") result.payload.upload.status = "selected_upload_unconfirmed";
    if (change === "upload-review") result.payload.upload.review_id = "not-a-review-id";
    if (change === "upload-continuity") result.payload.upload_continuity.document_id = "another_chrome_document";
    if (change === "answer") result.payload.reviewed_answers.first_name = "A different candidate";
    if (change === "assisted") result.payload.assisted_values.first_name = "Unapproved answer";
    if (change === "mapping") result.payload.field_mapping[0].label = "Different field";
    if (change === "required") result.payload.answers[0].value = "";
    if (change === "source") result.payload.artifact.resume_version_id = "unexpected_version";
    if (change === "candidate") result.payload.candidate_id = 0;
    if (change === "opening") result.payload.job.external_id = "not-an-opening";
    if (change === "opening-destination") result.payload.job.external_id = "4740333006";
    request.mockResolvedValueOnce(Response.json(result));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={application} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("response changed");
    expect(screen.queryByRole("checkbox")).toBeNull(); expect(screen.queryByRole("button", { name: /Approve final submission/ })).toBeNull();
    expect(request).toHaveBeenCalledTimes(1); expect(apiDownload).not.toHaveBeenCalled();
  });

  it("requires a fresh status load after an uncertain approval without issuing a submit or retry", async () => {
    await load(); request.mockResolvedValueOnce(Response.json({}, { status: 503 }));
    await userEvent.click(consent()); await userEvent.click(approve());
    expect(await screen.findByRole("alert")).toHaveTextContent("no automatic retry");
    expect(consent()).not.toBeChecked(); expect(consent()).toBeDisabled(); expect(approve()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Load current review status" })).toBeEnabled();
    expect(request).toHaveBeenCalledTimes(2); expect(apiDownload).not.toHaveBeenCalled();
  });

  it("refuses an approval response that replaces the exact final resume or answers", async () => {
    const draft = finalSubmitReview(); await load(draft);
    const changed = structuredClone(draft); changed.state = "approved";
    changed.payload.artifact.sha256 = "1".repeat(64);
    changed.payload.upload_continuity.artifact_sha256 = changed.payload.artifact.sha256;
    request.mockResolvedValueOnce(Response.json(changed));
    await userEvent.click(consent()); await userEvent.click(approve());
    expect(await screen.findByRole("alert")).toHaveTextContent("approved review changed");
    expect(screen.queryByText(/Final submission approved and 20 credits reserved/)).toBeNull();
    expect(consent()).toBeDisabled(); expect(approve()).toBeDisabled(); expect(request).toHaveBeenCalledTimes(2);
  });

  it("reports an unknown employer outcome and held credits truthfully without approval or automatic resend", async () => {
    await load(finalSubmitReview("unknown"));
    expect(screen.getByRole("status")).toHaveTextContent("No verified receipt is available; 20 credits remain held");
    expect(screen.getByRole("status")).toHaveTextContent("Do not resend automatically");
    expect(screen.queryByRole("checkbox")).toBeNull(); expect(screen.queryByRole("button", { name: /Approve final submission/ })).toBeNull();
    expect(screen.queryByText(/0 application credits/)).toBeNull(); expect(request).toHaveBeenCalledTimes(1);
  });

  it("retains final-submit uncertainty and credit disclosure after the private durable payload is erased", async () => {
    request.mockResolvedValueOnce(Response.json({ ...finalSubmitReview("unknown"), payload: {} }));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={application} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/20 credits.*held/);
    expect(screen.getByRole("status")).toHaveTextContent(/no.*receipt|receipt.*unverified/i);
    expect(screen.getByRole("status")).toHaveTextContent(/no.*resend|no.*retry/i);
    expect(screen.queryByText("Synthetic Alice")).toBeNull(); expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/fill outcome|filled only/)).toBeNull(); expect(screen.queryByRole("alert")).toBeNull(); expect(request).toHaveBeenCalledTimes(1);
  });

  it.each(["fee", "employer", "count", "status"])("rejects inconsistent erased final-submit %s metadata", async (change) => {
    const result = { ...finalSubmitReview("unknown"), payload: {} };
    if (change === "fee") result.fee_credits = 0;
    if (change === "employer") result.employer_confirmed = true;
    if (change === "count") result.filled_count = 1;
    if (change === "status") result.submission_status = "confirmed";
    request.mockResolvedValueOnce(Response.json(result));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("response changed");
    expect(screen.queryByRole("status")).toBeNull(); expect(screen.queryByRole("checkbox")).toBeNull(); expect(request).toHaveBeenCalledTimes(1);
  });

  it.each(["confirmed", "verified_not_submitted"])("renders independently reconciled %s terminal state without presenting another submit action", async (status) => {
    const confirmed = status === "confirmed";
    request.mockResolvedValueOnce(Response.json({ ...finalSubmitReview(confirmed ? "filled" : "cancelled"), payload: {},
      filled_count: confirmed ? 1 : 0, submission_status: status, employer_confirmed: confirmed }));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={application} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("status")).toHaveTextContent(confirmed ? /receipt.*verified|verified.*receipt/i : /verified.*no application was submitted|verified.*not.*submitted|verified.*non.*submission/i);
    expect(screen.queryByText(/20 credits.*held/)).toBeNull(); expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /Approve final submission/ })).toBeNull(); expect(screen.queryByRole("alert")).toBeNull(); expect(request).toHaveBeenCalledTimes(1);
  });
});

describe("candidate-entered hosted country and employment review", () => {
  async function load(result: ReturnType<typeof hostedReview>) {
    request.mockResolvedValueOnce(Response.json(result));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={application} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    await screen.findByRole("button", { name: "Save exact answers" });
  }
  const acceptance = () => screen.getByRole("checkbox", { name: /I reviewed the exact file, destination and answers/ });

  it("saves only explicit country and employment entries, then separately approves the refreshed exact digest", async () => {
    const initial = hostedReview();
    const answers = { country: "in", "company-name-0": "Owned fixture company", "title-0": "Engineer", "start-date-month-0": "January", "start-date-year-0": "2020", "end-date-month-0": "March", "end-date-year-0": "2024" };
    const revised = revisedHostedReview(initial, answers);
    await load(initial);
    request.mockResolvedValueOnce(Response.json(revised)).mockResolvedValueOnce(Response.json({ ...revised, state: "approved" }));
    expect(screen.getByText(/not citizenship or work authorization/)).toBeVisible();
    expect(screen.getByLabelText("Country (phone country code)")).toHaveValue("");
    expect(screen.getByLabelText("Company name · employment 1")).toHaveValue("");
    expect(screen.getByLabelText("Current role · employment 1")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save exact answers" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Download exact review file" }));
    expect(acceptance()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Approve reviewed form assistance" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Country (phone country code)"), "in");
    fireEvent.change(screen.getByLabelText("Company name · employment 1"), { target: { value: answers["company-name-0"] } });
    fireEvent.change(screen.getByLabelText("Title · employment 1"), { target: { value: answers["title-0"] } });
    await userEvent.selectOptions(screen.getByLabelText("Start date month · employment 1"), "January");
    fireEvent.change(screen.getByLabelText("Start date year · employment 1"), { target: { value: "2020" } });
    await userEvent.selectOptions(screen.getByLabelText("End date month · employment 1"), "March");
    fireEvent.change(screen.getByLabelText("End date year · employment 1"), { target: { value: "2024" } });
    expect(request).toHaveBeenCalledTimes(1); expect(acceptance()).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save exact answers" }));
    expect(await screen.findByText(/Exact answers saved for this review/)).toBeVisible();
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, review_digest: initial.review_digest, hosted_answers: answers });
    expect(request.mock.calls[1][0]).toBe("/api/browser-assistance/candidate/revise");
    expect(acceptance()).not.toBeChecked(); expect(acceptance()).toBeEnabled();
    expect(screen.getByText("Owned fixture company")).toBeVisible();
    await userEvent.click(acceptance()); await userEvent.click(screen.getByRole("button", { name: "Approve reviewed form assistance" }));
    expect(await screen.findByText(/Form assistance approved/)).toBeVisible();
    expect(JSON.parse(request.mock.calls[2][1].body)).toEqual({ identity, confirmed: true, review_digest: revised.review_digest });
    expect(request.mock.calls.map(([url]) => url)).toEqual(["/api/browser-assistance/candidate/review", "/api/browser-assistance/candidate/revise", "/api/browser-assistance/candidate/approve"]);
    expect(apiDownload).toHaveBeenCalledTimes(1);
  });

  it("clears existing acceptance immediately on an unsaved edit and requires a separate save before approval", async () => {
    await load(hostedReview(undefined, true));
    await userEvent.click(screen.getByRole("button", { name: "Download exact review file" }));
    await userEvent.click(acceptance()); expect(acceptance()).toBeChecked();
    fireEvent.change(screen.getByLabelText("Company name · employment 1"), { target: { value: "Owned employer" } });
    expect(acceptance()).not.toBeChecked(); expect(acceptance()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Approve reviewed form assistance" })).toBeDisabled();
    expect(screen.getByText(/Save your changed answers/)).toBeVisible(); expect(request).toHaveBeenCalledTimes(1);
  });

  it("saves an explicit no-op country choice as a zero-action draft and keeps approval unavailable", async () => {
    const initial = hostedReview(); await load(initial);
    request.mockResolvedValueOnce(Response.json(revisedHostedReview(initial, { country: "us" })));
    await userEvent.selectOptions(screen.getByLabelText("Country (phone country code)"), "us");
    await userEvent.click(screen.getByRole("button", { name: "Save exact answers" }));
    expect(await screen.findByText(/Exact answers saved for this review/)).toBeVisible();
    expect(screen.getByText(/No changed answers to fill/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Download exact review file" }));
    expect(acceptance()).toBeDisabled(); expect(screen.getByRole("button", { name: "Approve reviewed form assistance" })).toBeDisabled();
    expect(JSON.parse(request.mock.calls[1][1].body).hosted_answers).toEqual({ country: "us" }); expect(request).toHaveBeenCalledTimes(2);
  });

  it("clears a saved last action and restores its required manual field without extending the review", async () => {
    const initial = revisedHostedReview(hostedReview(), { "company-name-0": "Owned employer" }); await load(initial);
    const revised = revisedHostedReview(initial, {}); revised.review_digest = "3".repeat(64);
    request.mockResolvedValueOnce(Response.json(revised));
    expect(screen.queryByText("Company name", { selector: "li" })).toBeNull();
    await userEvent.clear(screen.getByLabelText("Company name · employment 1"));
    await userEvent.click(screen.getByRole("button", { name: "Save exact answers" }));
    expect(await screen.findByText(/Exact answers saved for this review/)).toBeVisible();
    expect(screen.getByText("Company name", { selector: "li" })).toBeVisible();
    expect(screen.getByText(/No changed answers to fill/)).toBeVisible();
    expect(acceptance()).toBeDisabled(); expect(JSON.parse(request.mock.calls[1][1].body).hosted_answers).toEqual({});
    expect(revised.expires_at_ms).toBe(initial.expires_at_ms); expect(request).toHaveBeenCalledTimes(2);
  });

  it("lets the candidate request a current-role toggle or Add only as reviewed actions, with conditional controls requiring fresh inspection", async () => {
    const initial = hostedReview(); await load(initial);
    await userEvent.selectOptions(screen.getByLabelText("End date month · employment 1"), "March");
    fireEvent.change(screen.getByLabelText("End date year · employment 1"), { target: { value: "2024" } });
    await userEvent.selectOptions(screen.getByLabelText("Current role · employment 1"), "true");
    expect(screen.getByLabelText("End date month · employment 1")).toBeDisabled();
    expect(screen.getByLabelText("End date month · employment 1")).toHaveValue("");
    expect(screen.getByLabelText("End date year · employment 1")).toHaveValue("");
    await userEvent.click(screen.getByLabelText("Add another employment row (2 total)"));
    const answers = { "current-role-0_1": "true", "employment-add-1": "2" };
    request.mockResolvedValueOnce(Response.json(revisedHostedReview(initial, answers)));
    await userEvent.click(screen.getByRole("button", { name: "Save exact answers" }));
    await screen.findByText(/Exact answers saved for this review/);
    expect(JSON.parse(request.mock.calls[1][1].body).hosted_answers).toEqual(answers);
    expect(screen.getByText(/create a fresh review before filling newly visible controls/)).toBeVisible();
    expect(screen.getByText("Add row 2")).toBeVisible(); expect(request).toHaveBeenCalledTimes(2);
  });

  it("preserves five distinct employment rows and omits end controls for an observed current role", async () => {
    const rows = Array.from({ length: 5 }, (_, index) => ({ index, current_role: index === 2 }));
    await load(hostedReview(rows));
    for (let index = 1; index <= 5; index++) expect(screen.getByLabelText(`Company name · employment ${index}`)).toHaveValue("");
    expect(screen.queryByLabelText("End date month · employment 3")).toBeNull();
    expect(screen.queryByLabelText(/Add another employment row/)).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Current role · employment 3"), "false");
    expect(screen.queryByLabelText("End date month · employment 3")).toBeNull();
    expect(screen.getByRole("button", { name: "Save exact answers" })).toBeEnabled();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("keeps malformed years, reversed dates and whitespace out of a saved draft", async () => {
    await load(hostedReview());
    fireEvent.change(screen.getByLabelText("Company name · employment 1"), { target: { value: " Owned employer" } });
    expect(screen.getByRole("button", { name: "Save exact answers" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Company name · employment 1"));
    fireEvent.change(screen.getByLabelText("Start date year · employment 1"), { target: { value: "020" } });
    expect(screen.getByRole("button", { name: "Save exact answers" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Start date year · employment 1"));
    fireEvent.change(screen.getByLabelText("Start date year · employment 1"), { target: { value: "2025" } });
    await userEvent.selectOptions(screen.getByLabelText("Start date month · employment 1"), "December");
    await userEvent.selectOptions(screen.getByLabelText("End date month · employment 1"), "January");
    fireEvent.change(screen.getByLabelText("End date year · employment 1"), { target: { value: "2025" } });
    expect(screen.getByRole("button", { name: "Save exact answers" })).toBeDisabled(); expect(request).toHaveBeenCalledTimes(1);
  });

  it.each(["country-label", "country-option", "country-count", "month-value", "employment-id", "employment-label", "control-type", "current-role", "extra-control", "extra-row", "duplicate-row", "value-id", "button-value", "protocol"])("refuses unsupported hosted descriptors before draft entry or approval: %s", async (change) => {
    const result = hostedReview(); const hosted = result.payload.hosted;
    if (change === "country-label") hosted.controls[0].label = "Citizenship";
    if (change === "country-option") hosted.controls[0].options![0].label = "Unknown country label";
    if (change === "country-count") hosted.controls[0].options!.pop();
    if (change === "month-value") hosted.controls[3].options = [{ value: "1", label: "January" }];
    if (change === "employment-id") hosted.controls[1].id = "company-name-9";
    if (change === "employment-label") hosted.controls[1].label = "Work authorization";
    if (change === "control-type") hosted.controls[1].type = "select";
    if (change === "current-role") hosted.values["current-role-0_1"] = "true";
    if (change === "extra-control") hosted.controls.push({ id: "authorization", label: "Authorization", type: "checkbox", required: true });
    if (change === "extra-row") hosted.rows = Array.from({ length: 6 }, (_, index) => ({ index, current_role: false }));
    if (change === "duplicate-row") hosted.rows.push(hosted.rows[0]);
    if (change === "value-id") hosted.values["company-name-9"] = "Unknown company";
    if (change === "button-value") hosted.values["employment-add-1"] = "2";
    if (change === "protocol") result.payload.review_protocol_version = 1;
    request.mockResolvedValueOnce(Response.json(result));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("response changed");
    expect(screen.queryByRole("button", { name: "Save exact answers" })).toBeNull(); expect(apiDownload).not.toHaveBeenCalled();
  });

  it.each(["file", "destination", "context", "document", "frame", "mapping", "expiry", "answer", "digest", "base-answer", "options"])("rejects a revision response that changes unapproved review data: %s", async (change) => {
    const initial = hostedReview(undefined, true); await load(initial);
    const revised = revisedHostedReview(initial, { country: "in" });
    if (change === "file") revised.payload.artifact.sha256 = "9".repeat(64);
    if (change === "destination") revised.payload.target_url = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4739199005";
    if (change === "context") revised.payload.context_digest = "9".repeat(64);
    if (change === "document") revised.payload.document_id = "unreviewed-document";
    if (change === "frame") revised.payload.frame_id = 1;
    if (change === "mapping") revised.payload.field_mapping[0].label = "Changed mapping";
    if (change === "expiry") revised.expires_at_ms++;
    if (change === "answer") { revised.payload.hosted.draft_answers.country = "us"; revised.payload.fields.pop(); delete revised.payload.reviewed_answers.country; }
    if (change === "digest") revised.review_digest = initial.review_digest;
    if (change === "base-answer") { revised.payload.fields[0].value = "Changed automatically"; revised.payload.reviewed_answers.first_name = "Changed automatically"; }
    if (change === "options") revised.payload.hosted.controls[0].options![0].label = "Changed country";
    request.mockResolvedValueOnce(Response.json(revised));
    await userEvent.selectOptions(screen.getByLabelText("Country (phone country code)"), "in");
    await userEvent.click(screen.getByRole("button", { name: "Save exact answers" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/response changed|saved answer review changed/);
    expect(screen.queryByText(/Exact answers saved for this review/)).toBeNull(); expect(acceptance()).toBeDisabled();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("never retries a lost revision and requires a status load before another save or approval", async () => {
    await load(hostedReview()); request.mockRejectedValueOnce(new Error("Synthetic lost revision reply"));
    await userEvent.selectOptions(screen.getByLabelText("Country (phone country code)"), "in");
    await userEvent.click(screen.getByRole("button", { name: "Save exact answers" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Synthetic lost revision reply");
    expect(screen.getByLabelText("Country (phone country code)")).toBeDisabled(); expect(acceptance()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Load current review status" })).toBeEnabled(); expect(request).toHaveBeenCalledTimes(2);
  });

  it.each([{ version: 2, count: 64, valid: true }, { version: 2, count: 65, valid: false }, { version: 1, count: 8, valid: false }])("retains protocol-specific terminal cursor bounds: %j", async ({ version, count, valid }) => {
    request.mockResolvedValueOnce(Response.json({ ...review("filled"), review_protocol_version: version === 1 ? undefined : version, filled_count: count, payload: {} }));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    if (valid) expect(await screen.findByRole("status")).toHaveTextContent("filled only");
    else expect(await screen.findByRole("alert")).toHaveTextContent("response changed");
  });
});

describe("candidate reviewed browser assistance", () => {
  it("requires explicit attachment approval before any file download or companion binary disclosure", async () => {
    request.mockResolvedValueOnce(Response.json(attachmentReview())).mockResolvedValueOnce(Response.json(attachmentReview("approved")));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    const approve = await screen.findByRole("button", { name: "Approve exact attachment assistance" });
    expect(approve).toBeDisabled(); expect(screen.getByRole("checkbox")).toBeEnabled();
    expect(screen.getByText("original resume #7")).toBeVisible(); expect(screen.getByText("artifact_owned")).toBeVisible();
    expect(screen.getByRole("link", { name: target })).toHaveAttribute("href", target);
    expect(screen.getByText(/Selecting it can immediately disclose or upload its bytes/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Download exact review file" })).toBeNull(); expect(screen.queryByText("Answers to fill")).toBeNull();
    expect(request).toHaveBeenCalledTimes(1); expect(apiDownload).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(approve);
    expect(await screen.findByText(/Attachment assistance approved/)).toBeVisible(); expect(apiDownload).not.toHaveBeenCalled();
    expect(request.mock.calls.map(([url]) => url)).toEqual(["/api/browser-assistance/candidate/review", "/api/browser-assistance/candidate/approve"]);
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true, review_digest: "b".repeat(64) });
    expect(screen.queryByRole("button", { name: "Approve exact attachment assistance" })).toBeNull();
  });

  it("binds attachment approval to the exact saved source and rejects changed source metadata", async () => {
    const changed = attachmentReview("approved"); changed.payload.artifact.resume_id = 8;
    request.mockResolvedValueOnce(Response.json(attachmentReview())).mockResolvedValueOnce(Response.json(changed));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    await screen.findByRole("button", { name: "Approve exact attachment assistance" });
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(screen.getByRole("button", { name: "Approve exact attachment assistance" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approved review changed");
    expect(screen.queryByText(/Attachment assistance approved/)).toBeNull(); expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(apiDownload).not.toHaveBeenCalled(); expect(request).toHaveBeenCalledTimes(2);
  });

  it.each(["fields", "answers", "action", "input", "expected-empty", "source", "filename", "version", "status", "count"])("refuses an invalid attachment %s before offering approval or fetching bytes", async (change) => {
    const result = attachmentReview();
    if (change === "fields") result.payload.fields = review().payload.fields;
    if (change === "answers") result.payload.reviewed_answers = { first_name: "Unapproved text" };
    if (change === "action") result.payload.allowed_actions = ["fill", "attach"];
    if (change === "input") result.payload.attachment.input_id = "cover_letter";
    if (change === "expected-empty") result.payload.attachment.expected_empty = false;
    if (change === "source") result.payload.artifact.resume_choice = "tailored";
    if (change === "filename") result.payload.artifact.filename = "résumé.pdf";
    if (change === "version") result.review_protocol_version = 2;
    if (change === "status") result.attachment_status = "uploaded";
    if (change === "count") { result.state = "filled"; result.attachment_status = "selected_upload_unconfirmed"; }
    request.mockResolvedValueOnce(Response.json(result));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("response changed");
    expect(screen.queryByRole("button", { name: "Approve exact attachment assistance" })).toBeNull();
    expect(apiDownload).not.toHaveBeenCalled(); expect(request).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])("labels local attachment selection honestly, including after payload erasure (cleared=%s)", async (cleared) => {
    const result = attachmentReview("filled");
    request.mockResolvedValueOnce(Response.json(cleared ? { ...result, payload: {} } : result));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Resume selected locally. Employer upload is unconfirmed.");
    expect(screen.getByRole("status")).toHaveTextContent("No application receipt or fee is established");
    expect(screen.queryByText(/answers filled/)).toBeNull(); expect(apiDownload).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Approve exact attachment assistance" })).toBeNull();
  });

  it.each(["unknown", "cancelled", "expired"])("retains attachment context for privacy-cleared %s status", async (state) => {
    request.mockResolvedValueOnce(Response.json({ ...attachmentReview(state), payload: {} }));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("status")).toHaveTextContent(state === "unknown" ? "file selection or upload outcome is uncertain" : state === "cancelled" ? "attachment assistance cancelled" : "expired");
    expect(screen.queryByRole("button", { name: "Approve exact attachment assistance" })).toBeNull(); expect(apiDownload).not.toHaveBeenCalled();
  });

  it("shows and approves explicit typed answers only after the exact resume is reviewed", async () => {
    request.mockResolvedValueOnce(Response.json(typedReview())).mockResolvedValueOnce(Response.json(typedReview("approved")));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    const approve = await screen.findByRole("button", { name: "Approve reviewed form assistance" });
    expect(approve).toBeDisabled();
    expect(screen.getByText("Explicit candidate choice")).toBeVisible();
    expect(screen.queryByText("owned-choice")).toBeNull();
    expect(screen.getByText("+919000000000")).toBeVisible();
    expect(screen.getByText("Country")).toBeVisible();
    await acceptReview(); await userEvent.click(approve);
    expect(await screen.findByText(/Form assistance approved/)).toBeVisible();
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true, review_digest: "b".repeat(64) });
    expect(request.mock.calls.map(([url]) => url)).toEqual(["/api/browser-assistance/candidate/review", "/api/browser-assistance/candidate/approve"]);
  });

  it("rejects a changed choice label even when the response contains a matching new options list", async () => {
    const changed = typedReview("approved");
    const field = changed.payload.fields.at(-1)!;
    if (!("options" in field)) throw new Error("Expected typed fixture option");
    field.options[0].label = "Changed after candidate approval";
    field.value_label = "Changed after candidate approval";
    request.mockResolvedValueOnce(Response.json(typedReview())).mockResolvedValueOnce(Response.json(changed));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    await screen.findByRole("button", { name: "Approve reviewed form assistance" });
    await acceptReview(); await userEvent.click(screen.getByRole("button", { name: "Approve reviewed form assistance" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approved review changed");
    expect(request).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/Form assistance approved/)).toBeNull();
  });

  it("does not request, approve or download anything before an explicit valid review", async () => {
    const view = render(<BrowserAssistanceReview deviceId={null} reviewId={null} applicationId={application} />);
    expect(screen.getByText(application)).toBeVisible(); expect(request).not.toHaveBeenCalled(); expect(apiDownload).not.toHaveBeenCalled();
    view.rerender(<BrowserAssistanceReview deviceId="invalid" reviewId={null} applicationId={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("valid device or application review"); expect(request).not.toHaveBeenCalled();
    view.rerender(<BrowserAssistanceReview deviceId={identity} reviewId={identity} applicationId={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("valid device or application review"); expect(request).not.toHaveBeenCalled();
  });

  it("confirms only the exact device key after a candidate checkbox using the current account CSRF", async () => {
    request.mockResolvedValueOnce(Response.json(device())).mockResolvedValueOnce(Response.json(device("active")));
    render(<BrowserAssistanceReview deviceId={identity} reviewId={null} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Review device connection" }));
    const confirm = await screen.findByRole("button", { name: "Confirm this device" }); expect(confirm).toBeDisabled();
    expect(screen.getByText("a".repeat(64))).toBeVisible(); expect(screen.queryByLabelText("HireWiz password")).toBeNull();
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(confirm);
    expect(await screen.findByText(/Device confirmation saved/)).toBeVisible();
    expect(request).toHaveBeenCalledTimes(2); expect(accountMutationHeaders).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[1][0]).toBe("/api/browser-assistance/candidate/confirm");
    expect(request.mock.calls[1][1].headers["X-Hirewiz-Account-CSRF"]).toBe("9".repeat(64));
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true, key_sha256: "a".repeat(64) });
    expect(apiDownload).not.toHaveBeenCalled();
  });

  it("keeps an active device usable after its initial pending connection expiry", async () => {
    request.mockResolvedValueOnce(Response.json({ ...device("active"), expires_at_ms: Date.now() - 1000 }));
    render(<BrowserAssistanceReview deviceId={identity} reviewId={null} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Review device connection" }));
    expect(await screen.findByRole("button", { name: "Revoke this device" })).toBeEnabled();
    expect(screen.queryByText(/This review expired/)).toBeNull(); expect(screen.queryByRole("button", { name: "Confirm this device" })).toBeNull();
  });

  it("does not claim confirmation when its response changes the reviewed device key", async () => {
    request.mockResolvedValueOnce(Response.json(device())).mockResolvedValueOnce(Response.json({ ...device("active"), key_sha256: "0".repeat(64) }));
    render(<BrowserAssistanceReview deviceId={identity} reviewId={null} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Review device connection" }));
    await userEvent.click(await screen.findByRole("checkbox")); await userEvent.click(screen.getByRole("button", { name: "Confirm this device" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("device key or state changed");
    expect(screen.queryByText(/Device confirmation saved/)).toBeNull(); expect(screen.getByRole("checkbox")).toBeDisabled(); expect(request).toHaveBeenCalledTimes(2);
  });

  it("binds approval to the downloaded file, exact answers and review digest; never submits or uploads", async () => {
    request.mockResolvedValueOnce(Response.json(review())).mockResolvedValueOnce(Response.json(review("approved")));
    await loadReview(); const approve = screen.getByRole("button", { name: "Approve reviewed text assistance" });
    expect(approve).toBeDisabled(); expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByText("Synthetic Alice")).toBeVisible(); expect(screen.getByText("synthetic@example.test")).toBeVisible();
    expect(screen.getByText("Gender")).toBeVisible(); expect(screen.getByText(/Email blur can send/)).toBeVisible();
    expect(screen.getByText(/A field already authorized may finish within ten seconds/)).toBeVisible();
    expect(screen.getByRole("link", { name: target })).toHaveAttribute("href", target);
    await acceptReview(); expect(approve).toBeEnabled(); await userEvent.click(approve);
    expect(await screen.findByText(/Text assistance approved/)).toBeVisible();
    expect(apiDownload).toHaveBeenCalledExactlyOnceWith(`/v1/employer-jobs/applications/${application}/artifact`, "Synthetic exact resume.pdf");
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true, review_digest: "b".repeat(64) });
    expect(request.mock.calls.map(([url]) => url)).toEqual(["/api/browser-assistance/candidate/review", "/api/browser-assistance/candidate/approve"]);
    expect(screen.queryByRole("button", { name: "Approve reviewed text assistance" })).toBeNull();
  });

  it("reviews the current five supported semantic fields with this job's distinct question IDs", async () => {
    const result = review(); result.payload.target_url = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4739199005";
    result.payload.fields = [
      { id: "first_name", label: "First Name", type: "text", value: "Synthetic Alice", expected_before: "" },
      { id: "last_name", label: "Last Name", type: "text", value: "Fixture", expected_before: "" },
      { id: "email", label: "Email", type: "text", value: "synthetic@example.test", expected_before: "" },
      { id: "question_9123507005", label: "LinkedIn Profile", type: "text", value: "https://example.test/current-profile", expected_before: "" },
      { id: "question_9123508005", label: "Total Years of Experience", type: "text", value: "10", expected_before: "" },
    ];
    request.mockResolvedValueOnce(Response.json(result)); await loadReview();
    expect(screen.getByText("LinkedIn Profile")).toBeVisible(); expect(screen.getByText("Total Years of Experience")).toBeVisible();
    expect(screen.getByText("https://example.test/current-profile")).toBeVisible(); expect(screen.queryByText("Website")).toBeNull(); expect(screen.queryByText("Current Location")).toBeNull();
    expect(request).toHaveBeenCalledTimes(1); expect(screen.getByRole("button", { name: "Approve reviewed text assistance" })).toBeDisabled();
  });

  it("does not enable approval when the exact file download fails", async () => {
    request.mockResolvedValueOnce(Response.json(review())); vi.mocked(apiDownload).mockRejectedValue(new Error("Exact file unavailable"));
    await loadReview(); await userEvent.click(screen.getByRole("button", { name: "Download exact review file" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Exact file unavailable");
    expect(screen.getByRole("checkbox")).toBeDisabled(); expect(screen.getByRole("button", { name: "Approve reviewed text assistance" })).toBeDisabled(); expect(request).toHaveBeenCalledTimes(1);
  });

  it("does not claim approval for a response that replaces the exact reviewed answer payload", async () => {
    const changed = review("approved"); changed.payload.fields[0].value = "Different approved answer";
    request.mockResolvedValueOnce(Response.json(review())).mockResolvedValueOnce(Response.json(changed));
    await loadReview(); await acceptReview(); await userEvent.click(screen.getByRole("button", { name: "Approve reviewed text assistance" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approved review changed"); expect(screen.queryByText(/Text assistance approved/)).toBeNull();
    expect(screen.getByRole("checkbox")).toBeDisabled(); expect(request).toHaveBeenCalledTimes(2);
  });

  it("requires a fresh status load after an unavailable possibly consumed approval and never retries automatically", async () => {
    request.mockResolvedValueOnce(Response.json(review())).mockResolvedValueOnce(Response.json({ detail: "untrusted-secret" }, { status: 503 }));
    await loadReview(); await acceptReview(); await userEvent.click(screen.getByRole("button", { name: "Approve reviewed text assistance" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no automatic retry"); expect(screen.getByRole("alert")).not.toHaveTextContent("untrusted-secret");
    expect(request).toHaveBeenCalledTimes(2); expect(screen.getByRole("checkbox")).toBeDisabled(); expect(screen.getByRole("button", { name: "Load current review status" })).toBeEnabled();
  });

  it("returns Google authentication to this exact review without password enrollment", async () => {
    request.mockResolvedValueOnce(Response.json({}, { status: 401 }));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    await userEvent.click(await screen.findByRole("button", { name: "Sign in with Google" }));
    expect(signIn).toHaveBeenCalledExactlyOnceWith("google", { callbackUrl: `/browser-companion?review=${identity}` }); expect(request).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  });

  it.each(["destination", "action", "field", "semantic", "base-id", "type", "digest", "duplicate", "application"])("refuses a changed %s response before presenting an approval", async (change) => {
    const result = review();
    if (change === "destination") result.payload.target_url = "https://job-boards.greenhouse.io/another-company/jobs/4723040005";
    if (change === "action") result.payload.allowed_actions = ["submit"];
    if (change === "field") result.payload.fields[0].id = "question_8970458005";
    if (change === "semantic") result.payload.fields[0] = { ...result.payload.fields[0], id: "question_9123509005", label: "Gender" };
    if (change === "base-id") result.payload.fields[0].id = "foreign_first_name";
    if (change === "type") result.payload.fields[0].type = "select-one";
    if (change === "digest") result.review_digest = "invalid";
    if (change === "duplicate") result.payload.fields.push(result.payload.fields[0]);
    if (change === "application") result.payload.application_id = "other_application";
    request.mockResolvedValueOnce(Response.json(result));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={change === "application" ? application : null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("response changed");
    expect(screen.queryByRole("button", { name: "Approve reviewed text assistance" })).toBeNull(); expect(request).toHaveBeenCalledTimes(1); expect(apiDownload).not.toHaveBeenCalled();
  });

  it("cancels further assistance for an uncertain review without claiming previous disclosure was recalled", async () => {
    request.mockResolvedValueOnce(Response.json(review("unknown"))).mockResolvedValueOnce(Response.json(review("cancelled")));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("status")).toHaveTextContent("uncertain");
    expect(screen.queryByRole("button", { name: "Approve reviewed text assistance" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Cancel further assistance" }));
    expect(await screen.findByText(/Answers already filled or disclosed may remain/)).toBeVisible();
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true }); expect(request.mock.calls[1][0]).toBe("/api/browser-assistance/candidate/cancel");
  });

  it("clears previously displayed candidate answers when cancellation returns a privacy-cleared payload", async () => {
    request.mockResolvedValueOnce(Response.json(review())).mockResolvedValueOnce(Response.json({ ...review("cancelled"), payload: {} }));
    await loadReview(); await userEvent.click(screen.getByRole("button", { name: "Cancel further assistance" }));
    expect(await screen.findByText(/Already disclosed answers may remain/)).toBeVisible();
    expect(screen.queryByText("Synthetic Alice")).toBeNull(); expect(screen.queryByText("synthetic@example.test")).toBeNull();
    expect(screen.queryByRole("button", { name: "Download exact review file" })).toBeNull(); expect(screen.queryByRole("alert")).toBeNull();
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true });
  });

  it.each(["filled", "unknown", "expired"])("renders %s with a privacy-cleared terminal payload without inventing approval", async (state) => {
    request.mockResolvedValueOnce(Response.json({ ...review(state), payload: {} }));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
    expect(await screen.findByRole("status")).toHaveTextContent(state === "unknown" ? "uncertain" : state === "filled" ? "filled only" : "expired");
    expect(screen.queryByRole("button", { name: "Approve reviewed text assistance" })).toBeNull(); expect(screen.queryByRole("alert")).toBeNull();
  });

  it("supports a retained 10 MiB review file without imposing the unrelated browser upload limit", async () => {
    const result = review(); result.payload.artifact.size_bytes = 10 * 1024 * 1024; request.mockResolvedValueOnce(Response.json(result));
    await loadReview(); expect(screen.getByRole("button", { name: "Download exact review file" })).toBeEnabled(); expect(request).toHaveBeenCalledTimes(1);
  });

  it("revokes only the displayed device with an explicit confirmed request", async () => {
    request.mockResolvedValueOnce(Response.json(device("active"))).mockResolvedValueOnce(Response.json(device("revoked")));
    render(<BrowserAssistanceReview deviceId={identity} reviewId={null} applicationId={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Review device connection" }));
    await userEvent.click(await screen.findByRole("button", { name: "Revoke this device" }));
    expect(await screen.findByText(/Device access revoked/)).toBeVisible();
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true });
    expect(request.mock.calls[1][0]).toBe("/api/browser-assistance/candidate/revoke");
  });

  it("refuses an expired pending review and performs no automatic approval", async () => {
    request.mockResolvedValueOnce(Response.json({ ...review(), expires_at_ms: Date.now() - 1000 }));
    await loadReview(); await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("expired"));
    expect(screen.getByRole("button", { name: "Approve reviewed text assistance" })).toBeDisabled(); expect(request).toHaveBeenCalledTimes(1);
  });

  it("aborts an unmounted in-flight load and never replaces a review with its late response", async () => {
    let resolve!: (result: Response) => void; request.mockImplementation(() => new Promise<Response>((done) => { resolve = done; }));
    const view = render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={null} />);
    fireEvent.click(screen.getByRole("button", { name: "Load exact application review" })); await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    const signal: AbortSignal = request.mock.calls[0][1].signal; view.unmount(); expect(signal.aborted).toBe(true);
    await act(async () => { resolve(Response.json(review())); }); expect(request).toHaveBeenCalledTimes(1);
  });
});


describe("native exact resume upload and employer receipt review", () => {
  async function load(value: unknown) {
    request.mockResolvedValueOnce(Response.json(value));
    render(<BrowserAssistanceReview deviceId={null} reviewId={identity} applicationId={application} />);
    await userEvent.click(screen.getByRole("button", { name: "Load exact application review" }));
  }
  it("shows the exact recipient, key, hashes and expiry before separate zero-credit upload approval", async () => {
    const draft = nativeUploadReview(); await load(draft);
    const approve = await screen.findByRole("button", { name: "Approve exact resume upload" });
    expect(approve).toBeDisabled();
    expect(screen.getByText(draft.payload.upload_plan.storage_origin)).toBeVisible();
    expect(screen.getByText("owned/exact resume.pdf")).toBeVisible();
    for (const value of ["1", "2", "3", "4"]) expect(screen.getByText(value.repeat(64))).toBeVisible();
    expect(screen.getByText(new Date(draft.payload.upload_plan.expires_at_ms).toISOString())).toBeVisible();
    expect(screen.getByText(/0 application credits charged for this resume upload/)).toBeVisible();
    expect(screen.getByRole("checkbox")).toHaveAccessibleName(/one upload.*does not authorize final submission/);
    request.mockResolvedValueOnce(Response.json({ ...draft, state: "approved" }));
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(approve);
    expect(await screen.findByRole("status")).toHaveTextContent("Exact resume upload approved");
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ identity, confirmed: true, review_digest: draft.review_digest });
    expect(apiDownload).not.toHaveBeenCalled(); expect(request).toHaveBeenCalledTimes(2);
  });
  it.each(["action", "transport", "origin", "key", "hash", "component", "extra-policy", "context", "time-zone", "ip", "cache", "https", "status", "expiry", "document", "provenance", "missing-provenance", "source", "crosscheck"])("rejects native upload %s tampering before consent", async change => {
    const draft = nativeUploadReview(), plan = draft.payload.upload_plan;
    if (change === "action") draft.payload.allowed_actions = ["attach"];
    if (change === "transport") plan.transport = "greenhouse-owned-upload-browser-https-v1";
    if (change === "origin") plan.storage_origin = "https://unreviewed.example";
    if (change === "key") plan.storage_key = "../another.pdf";
    if (change === "hash") plan.fields_sha256 = "not-a-hash";
    if (change === "component") delete (plan as Partial<typeof plan>).resume_component_sha256;
    if (change === "extra-policy") Object.assign(plan, { policy: "SECRET_RAW_POLICY", "x-amz-credential": "SECRET_AWS_CREDENTIAL" });
    if (change === "context") draft.payload.native_context.tab_id = -1;
    if (change === "time-zone") draft.payload.native_context.time_zone = "Wrong/TimeZone";
    if (change === "ip") plan.presign_evidence.ip = "999.999.999.999";
    if (change === "cache") plan.presign_evidence.from_cache = true;
    if (change === "https") plan.presign_evidence.https_validation = "ignored_errors";
    if (change === "status") plan.presign_evidence.status_code = 202;
    if (change === "expiry") plan.expires_at_ms = plan.presign_evidence.completed_at_ms;
    if (change === "document") draft.payload.document_id = "";
    if (change === "provenance") plan.body_provenance = "page_owned_file";
    if (change === "missing-provenance") delete (plan as Partial<typeof plan>).body_provenance;
    if (change === "source") plan.presign_evidence.request_body_source = "native_raw";
    if (change === "crosscheck") plan.presign_evidence.native_body_crosscheck = "raw";
    await load(draft);
    expect(await screen.findByRole("alert")).toHaveTextContent("response changed");
    expect(screen.queryByRole("checkbox")).toBeNull(); expect(screen.queryByText("SECRET_RAW_POLICY")).toBeNull();
    expect(screen.queryByText("SECRET_AWS_CREDENTIAL")).toBeNull(); expect(request).toHaveBeenCalledTimes(1);
  });
  it("closes approval before the shorter storage expiry without extending the review", async () => {
    const draft = nativeUploadReview(); draft.payload.upload_plan.expires_at_ms = Date.now() + 9000;
    await load(draft);
    const approve = await screen.findByRole("button", { name: "Approve exact resume upload" });
    await waitFor(() => expect(approve).toBeDisabled());
    expect(await screen.findByText(/This review expired/)).toBeVisible(); expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(request).toHaveBeenCalledTimes(1);
  });
  it.each(["key", "hash", "component", "context", "expiry", "proof"])("binds upload approval to unchanged %s", async change => {
    const draft = nativeUploadReview(); await load(draft); await screen.findByRole("checkbox");
    const result = structuredClone(draft); result.state = "approved";
    if (change === "key") result.payload.upload_plan.storage_key = "another/exact.pdf";
    if (change === "hash") result.payload.upload_plan.policy_sha256 = "a".repeat(64);
    if (change === "component") result.payload.upload_plan.resume_component_sha256 = "a".repeat(64);
    if (change === "context") result.payload.native_context.tab_id++;
    if (change === "expiry") result.payload.upload_plan.expires_at_ms++;
    if (change === "proof") result.payload.upload_plan.presign_evidence.request_id = "another-original-request";
    request.mockResolvedValueOnce(Response.json(result));
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(screen.getByRole("button", { name: "Approve exact resume upload" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approved review changed");
    expect(screen.getByRole("checkbox")).toBeDisabled(); expect(request).toHaveBeenCalledTimes(2);
  });
  it("cannot upgrade an old attachment approval to native upload", async () => {
    const draft = attachmentReview(), result = nativeUploadReview("approved");
    result.payload.artifact = draft.payload.artifact;
    await load(draft); await screen.findByRole("checkbox"); request.mockResolvedValueOnce(Response.json(result));
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(screen.getByRole("button", { name: "Approve exact attachment assistance" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approved review changed");
  });
  it.each([false, true])("reports native storage acceptance without implying final receipt after payload erasure=%s", async erased => {
    const result = nativeUploadReview("filled"); await load(erased ? { ...result, payload: {} } : result);
    expect(await screen.findByRole("status")).toHaveTextContent("Resume upload verified");
    expect(screen.getByRole("status")).toHaveTextContent("No application was submitted");
    expect(screen.getByRole("status")).toHaveTextContent("no application credits were charged");
    expect(screen.queryByText(/Employer upload is unconfirmed/)).toBeNull(); expect(screen.queryByRole("checkbox")).toBeNull();
  });
  it("reviews the native receipt contract and exact serialized application without inventing a receipt", async () => {
    await load(nativeFinalReview());
    expect(await screen.findByRole("button", { name: /Approve final submission/ })).toBeDisabled();
    expect(screen.getByText(/backend-verified complete direct employer HTTPS 200/)).toBeVisible();
    expect(screen.getByText("owned/exact resume.pdf")).toBeVisible(); expect(screen.getByText("6".repeat(64))).toBeVisible();
    expect(screen.getByText(/Employer receipt unverified/)).toBeVisible();
    expect(screen.queryByText(/0 application credits/)).toBeNull(); expect(screen.queryByText(/Employer application ID/)).toBeNull();
  });
  it.each(["missing-contract", "upload-id", "upload-hash", "upload-https", "upload-body", "answer", "time-zone", "extra-application", "priority", "upload-source", "upload-crosscheck"])("rejects mismatched native final %s", async change => {
    const result = nativeFinalReview();
    if (change === "missing-contract") delete (result.payload as Partial<typeof result.payload>).receipt_contract;
    if (change === "upload-id") result.payload.upload.native_upload.action_id = identity;
    if (change === "upload-hash") result.payload.upload.native_upload.artifact_sha256 = "1".repeat(64);
    if (change === "upload-https") result.payload.upload.native_upload.native.https_validation = "ignored_errors";
    if (change === "upload-body") result.payload.upload.native_upload.multipart_sha256 = "1".repeat(64);
    if (change === "upload-source") result.payload.upload.native_upload.native.request_body_source = "native_raw";
    if (change === "upload-crosscheck") result.payload.upload.native_upload.native.native_body_crosscheck = "none";
    if (change === "answer") result.payload.expected_application.first_name = "Different candidate";
    if (change === "time-zone") result.payload.native_context.time_zone = "UTC";
    if (change === "extra-application") Object.assign(result.payload.expected_application, { unreviewed: true });
    if (change === "priority") result.payload.expected_application.answers_attributes["9132967005"].priority = 1;
    await load(result); expect(await screen.findByRole("alert")).toHaveTextContent("response changed");
    expect(screen.queryByRole("checkbox")).toBeNull(); expect(request).toHaveBeenCalledTimes(1);
  });
  it("preserves distinct contiguous custom-question priorities supplied by the server rather than mapping order", () => {
    const result = nativeFinalReview(), payload = result.payload;
    const field = { id: "question_99", label: "Website", type: "text", required: false, value: "https://owned.example.test" };
    payload.answers.push(field);
    payload.field_mapping.push({ id: field.id, label: field.label, type: field.type, required: field.required });
    payload.reviewed_answers[field.id] = field.value; payload.assisted_values[field.id] = field.value;
    const entries = payload.expected_application.answers_attributes as Record<string, Record<string, unknown>>;
    entries["9132967005"].priority = 1; entries["99"] = { question_id: "99", priority: 0, text_value: field.value };
    expect(isHostedReceiptReview(payload)).toBe(true);
    entries["99"].priority = 1; expect(isHostedReceiptReview(payload)).toBe(false);
  });
  it.each(["application-digest", "native-request", "upload-plan-digest", "context", "upload-crosscheck"])("binds final approval to unchanged %s", async change => {
    const draft = nativeFinalReview(); await load(draft); await screen.findByRole("checkbox");
    const result = structuredClone(draft); result.state = "approved";
    if (change === "application-digest") result.payload.expected_application_sha256 = "7".repeat(64);
    if (change === "native-request") result.payload.upload.native_upload.native.request_id = "another-native-request";
    if (change === "upload-crosscheck") result.payload.upload.native_upload.native.native_body_crosscheck = "form_fields";
    if (change === "upload-plan-digest") result.payload.upload.upload_plan_sha256 = "7".repeat(64);
    if (change === "context") { result.payload.native_context.tab_id++; result.payload.upload.native_upload.tab_id++; }
    request.mockResolvedValueOnce(Response.json(result));
    await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(screen.getByRole("button", { name: /Approve final submission/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approved review changed");
  });
  it("accepts the qualified extension-owned upload with native form-field crosscheck only under the new contract", async () => {
    const result = nativeFinalReview(); result.payload.upload.native_upload.native.native_body_crosscheck = "form_fields";
    await load(result);
    expect(await screen.findByRole("button", { name: /Approve final submission/ })).toBeDisabled();
    expect(screen.getByText(/Employer receipt unverified/)).toBeVisible(); expect(screen.queryByRole("alert")).toBeNull();
  });
  it("shows a confirmed native final receipt only on consistent backend terminal metadata", async () => {
    const result = nativeFinalReview("filled"); result.submission_status = "confirmed"; result.employer_confirmed = true; result.filled_count = 1;
    await load(result);
    expect(await screen.findByText(/Employer receipt independently verified/)).toHaveTextContent("20 credits charged");
    expect(screen.queryByRole("checkbox")).toBeNull(); expect(screen.queryByRole("button", { name: /Approve final submission/ })).toBeNull();
    expect(screen.queryByText(/Employer application ID/)).toBeNull();
  });
});

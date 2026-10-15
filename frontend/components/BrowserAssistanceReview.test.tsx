import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { signIn } from "next-auth/react";
import { accountMutationHeaders } from "../lib/accountTransportClient";
import { apiDownload } from "../lib/api";
import BrowserAssistanceReview from "./BrowserAssistanceReview";

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

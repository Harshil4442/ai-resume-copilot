import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { parseUnifiedServiceQuote } from "../../lib/unifiedServiceQuotes";
import { ServiceChargeReview, type ServiceReviewContext } from "./ServiceChargeReview";
const data = { schema_version: "unified-service-quote-v1", quote_id: "csq_fixture", operation: "interview_questions", input_fingerprint: "a".repeat(64),
  route_id: "qualified-interview", route_version: "1", mode: "enhanced", tariff_version: "credit-services-v1", credits: 5,
  deliverable: "Eight distinct interview questions with preparation guidance.", supported_limits: { question_count: 8, max_file_bytes: 10000 },
  created_at: "2026-10-27T10:00:00Z", expires_at: "2026-10-27T10:15:00Z", server_time: "2026-10-27T10:14:59Z" };
const context: ServiceReviewContext = { operation: "interview_questions", inputFingerprint: data.input_fingerprint,
  resume: { id: 12, filename: "My original resume.pdf" }, job: { id: "job12", title: "Backend Engineer", employer: "Example Studio" } };
beforeEach(() => vi.useFakeTimers()); afterEach(() => vi.useRealTimers());
describe("service approval review", () => {
  it("shows selected inputs, exact deliverable, limits and charge before an explicit approval", () => {
    const approve = vi.fn(); render(<ServiceChargeReview quote={parseUnifiedServiceQuote(data)!} context={context} onAccept={approve} onRefresh={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText("My original resume.pdf")).toBeInTheDocument(); expect(screen.getByText("Backend Engineer · Example Studio")).toBeInTheDocument();
    expect(screen.getByText(data.deliverable)).toBeInTheDocument(); expect(screen.getByText("Questions included: 8")).toBeInTheDocument();
    expect(screen.getByText("Maximum file size in bytes: 10,000")).toBeInTheDocument(); expect(approve).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Approve 5 credits" })); expect(approve).toHaveBeenCalledExactlyOnceWith("csq_fixture");
  });
  it("invalidates approval when a caller's selected inputs or operation change", () => {
    const approve = vi.fn(); const view = render(<ServiceChargeReview quote={parseUnifiedServiceQuote(data)!} context={{ ...context, inputFingerprint: "b".repeat(64) }} onAccept={approve} onRefresh={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("selected inputs changed"); expect(screen.getByRole("button", { name: "Approve 5 credits" })).toBeDisabled();
    view.rerender(<ServiceChargeReview quote={parseUnifiedServiceQuote(data)!} context={{ ...context, operation: "resume_tailor" }} onAccept={approve} onRefresh={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve 5 credits" })); expect(approve).not.toHaveBeenCalled();
  });
  it("expires at the server deadline and requests another explicit review", async () => {
    const approve = vi.fn(), refresh = vi.fn(); render(<ServiceChargeReview quote={parseUnifiedServiceQuote(data)!} context={context} onAccept={approve} onRefresh={refresh} onCancel={vi.fn()} />);
    vi.setSystemTime(new Date("2020-01-01T00:00:00Z")); await act(async () => { vi.advanceTimersByTime(1000); });
    expect(screen.getByRole("alert")).toHaveTextContent("quote expired"); expect(screen.getByRole("button", { name: "Approve 5 credits" })).toBeDisabled();
    expect(approve).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "Refresh service quote" })); expect(refresh).toHaveBeenCalledOnce();
  });
  it("does not approve unnamed inputs and respects caller availability and busy state", () => {
    const props = { quote: parseUnifiedServiceQuote(data)!, context: { operation: context.operation, inputFingerprint: context.inputFingerprint }, onAccept: vi.fn(), onRefresh: vi.fn(), onCancel: vi.fn() };
    const view = render(<ServiceChargeReview {...props} />); expect(screen.getByRole("button", { name: "Approve 5 credits" })).toBeDisabled();
    view.rerender(<ServiceChargeReview {...props} context={context} disabled disabledReason="This service is currently unavailable." />);
    expect(screen.getByRole("status")).toHaveTextContent("currently unavailable"); expect(screen.getByRole("button", { name: "Approve 5 credits" })).toBeDisabled();
    view.rerender(<ServiceChargeReview {...props} context={context} busy />); expect(screen.getByRole("button", { name: "Starting approved work…" })).toBeDisabled(); expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });
  it("truthfully identifies a native service and leaves employer approval separate", () => {
    const quote = parseUnifiedServiceQuote({ ...data, operation: "verified_submission", mode: "native", credits: 3 })!;
    render(<ServiceChargeReview quote={quote} context={{ ...context, operation: "verified_submission" }} onAccept={vi.fn()} onRefresh={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText("No generative AI call in this service")).toBeInTheDocument(); expect(screen.getByText(/Employer submission also requires its separate review/)).toBeInTheDocument();
  });
});

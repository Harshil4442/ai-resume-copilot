import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CanaryParticipation } from "./CanaryParticipation";

const review = { status: "NOT_OPTED_IN", review_id: "fixture-canary-v1", review_sha256: "a".repeat(64),
  notice: "Participation is voluntary. Review every service price and keep control of your applications. Stop any time.\nYour existing work remains available.",
  notice_sha256: "b".repeat(64), runtime_sha256: "c".repeat(64), expires_at: "2030-10-29T00:00:00Z",
  can_opt_in: true, execution_flag_enabled: true, accepted_work_survives_revocation: true };
let current: Record<string, unknown>, posts: Record<string, unknown>[], reads: string[], query: QueryClient;
const fetcher = vi.fn(), changed = vi.fn();
const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
function mount() { return render(<QueryClientProvider client={query}><CanaryParticipation onChange={changed} /></QueryClientProvider>); }
async function confirm() {
  const box = await screen.findByRole("checkbox", { name: /I have read the participation notice/ });
  expect(box).not.toBeChecked();
  fireEvent.click(box); fireEvent.click(screen.getByRole("button", { name: "Review participation" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm participation" }));
}
beforeEach(() => {
  current = { ...review }; posts = []; reads = []; changed.mockReset();
  query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  fetcher.mockReset().mockImplementation(async (path: string, init: RequestInit) => {
    const url = new URL(path, "https://www.hirewizhq.com");
    expect(url.pathname).toBe("/api/backend/v1/credits/canary-enrollment");
    if (init?.method === "POST") {
      const payload = JSON.parse(init.body as string); posts.push(payload);
      current = { ...current, status: payload.choice === "opt_in" ? "OPTED_IN" : "NOT_OPTED_IN" };
      return response({ status: "CHOICE_RECORDED", ...payload, sequence: posts.length });
    }
    reads.push(url.search);
    return response(current);
  });
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => { cleanup(); query.clear(); vi.unstubAllGlobals(); });

it("shows the full plain-text notice and requires unchecked choice plus separate confirmation", async () => {
  mount();
  expect(await screen.findByLabelText("Participation notice")).toHaveTextContent("Your existing work remains available.");
  const reviewButton = screen.getByRole("button", { name: "Review participation" });
  expect(reviewButton).toBeDisabled(); expect(posts).toHaveLength(0);
  fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(reviewButton);
  expect(screen.getByRole("group", { name: "Confirm voluntary participation" })).toHaveFocus();
  expect(posts).toHaveLength(0); fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Review participation" })).toHaveFocus();
  expect(posts).toHaveLength(0); fireEvent.click(screen.getByRole("button", { name: "Review participation" })); fireEvent.click(screen.getByRole("button", { name: "Confirm participation" }));
  await screen.findByRole("button", { name: "Stop participating" });
  expect(posts).toHaveLength(1);
  expect(posts[0]).toEqual({ review_id: review.review_id, review_sha256: review.review_sha256,
    notice_sha256: review.notice_sha256, choice: "opt_in", confirmed_notice: true, idempotency_key: expect.any(String) });
  expect(reads).toEqual(["", `?review_id=${review.review_id}&review_sha256=${review.review_sha256}`]);
  expect(changed).toHaveBeenCalledOnce();
});

it("does not infer opt-in from a successful receipt when the fresh owned record is revoked", async () => {
  fetcher.mockImplementation(async (path: string, init: RequestInit) => {
    if (init?.method === "POST") { posts.push(JSON.parse(init.body as string)); return response({ status: "CHOICE_RECORDED", choice: "opt_in" }); }
    reads.push(path); return response(current);
  });
  mount(); await confirm();
  await screen.findByText("Your current choice is not opted in.");
  expect(screen.queryByRole("button", { name: "Stop participating" })).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it("keeps a retained paused review revocable and does not offer a new opt-in", async () => {
  current = { ...review, status: "OPTED_IN", can_opt_in: false, execution_flag_enabled: false };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Stop participating" }));
  await screen.findByText("Your current choice is not opted in.");
  expect(posts[0].choice).toBe("revoke"); expect(posts[0].confirmed_notice).toBe(false);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.getByText(/New pilot services are currently paused/)).toBeInTheDocument();
});

it("sends no second choice for an unknown response and rechecks the exact retained review", async () => {
  let release!: () => void;
  fetcher.mockImplementation(async (path: string, init: RequestInit) => {
    if (init?.method === "POST") { posts.push(JSON.parse(init.body as string)); throw new Error("Lost response"); }
    reads.push(path);
    if (reads.length === 2) throw new Error("Read unavailable");
    if (reads.length === 3) await new Promise<void>((resolve) => { release = resolve; });
    return response(current);
  });
  mount(); await confirm();
  const check = await screen.findByRole("button", { name: "Check participation status" });
  expect(posts).toHaveLength(1); expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  fireEvent.click(check); fireEvent.click(check);
  await waitFor(() => expect(reads).toHaveLength(3));
  await act(async () => release());
  await screen.findByText("Your current recorded choice has been refreshed.");
  expect(posts).toHaveLength(1); expect(reads[2]).toContain(review.review_sha256);
});

it("resets consent when the current invitation changes and never renders notice HTML", async () => {
  mount(); fireEvent.click(await screen.findByRole("checkbox"));
  current = { ...review, review_id: "rotated-canary-v2", review_sha256: "d".repeat(64), notice_sha256: "e".repeat(64),
    notice: "<img src=x onerror=alert(1)> A new complete participation notice needs its own explicit review and choice." };
  fireEvent.click(screen.getByRole("button", { name: "Refresh participation status" }));
  await screen.findByText(/A new complete participation notice/);
  expect(screen.getByRole("checkbox")).not.toBeChecked(); expect(document.querySelector("img")).toBeNull();
  expect(posts).toHaveLength(0);
});

it.each([{ status: "NOT_AVAILABLE" }, { ...review, notice_sha256: "invalid" }])("hides controls for an absent or malformed invitation", async (value) => {
  current = value; const view = mount();
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  expect(view.container).toBeEmptyDOMElement(); expect(posts).toHaveLength(0);
});

it("does not continue reads or callbacks after its owner surface unmounts", async () => {
  let complete!: (value: Response) => void;
  fetcher.mockImplementation(async (path: string, init: RequestInit) => {
    if (init?.method === "POST") { posts.push(JSON.parse(init.body as string)); return new Promise<Response>((resolve) => { complete = resolve; }); }
    reads.push(path); return response(current);
  });
  const view = mount(); await confirm(); await waitFor(() => expect(posts).toHaveLength(1));
  view.unmount(); await act(async () => complete(response({ status: "CHOICE_RECORDED" })));
  expect(reads).toHaveLength(1); expect(changed).not.toHaveBeenCalled();
});

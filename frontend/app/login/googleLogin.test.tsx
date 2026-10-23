import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocked = vi.hoisted(() => ({
  getProviders: vi.fn(),
  signIn: vi.fn(),
  prepareConsent: vi.fn(),
  push: vi.fn(),
  postLoginPath: vi.fn((path: string) => path),
}));

vi.mock("next-auth/react", () => ({
  getProviders: mocked.getProviders,
  signIn: mocked.signIn,
  useSession: () => ({ status: "unauthenticated" }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocked.push }) }));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props}>{children}</a>,
}));
vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));
vi.mock("../../lib/auth", () => ({ prepareGoogleRegistrationConsent: mocked.prepareConsent }));
vi.mock("../../lib/useHydrated", () => ({ useHydrated: () => true }));
vi.mock("../../lib/candidateAuthClient", () => ({
  candidateRegistration: vi.fn(),
  postLoginPath: mocked.postLoginPath,
}));

import LoginPage from "./page";

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getProviders.mockResolvedValue({ google: { id: "google", type: "oauth" } });
  mocked.signIn.mockResolvedValue(undefined);
  mocked.prepareConsent.mockResolvedValue(undefined);
  mocked.postLoginPath.mockImplementation((path: string) => path);
  window.history.replaceState({}, "", "/login");
});

afterEach(() => cleanup());

describe("Google sign-in redirect failures", () => {
  it("shows backend callback failure after returning to login and retains consent gating", async () => {
    window.history.replaceState({}, "", "/login?error=Callback&callbackUrl=%2Fdashboard");
    render(<LoginPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Sign-in is temporarily unavailable. Please try again later.");
    expect(await screen.findByRole("button", { name: "Continue with Google" })).toBeDisabled();
    expect(mocked.prepareConsent).not.toHaveBeenCalled();
    expect(mocked.signIn).not.toHaveBeenCalled();
  });

  it("distinguishes an OAuth verification failure from a backend callback failure", async () => {
    window.history.replaceState({}, "", "/login?error=OAuthCallback");
    render(<LoginPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Google sign-in could not be verified. Please start sign-in again.");
    expect(mocked.signIn).not.toHaveBeenCalled();
  });

  it("does not render arbitrary error or callback values", async () => {
    window.history.replaceState({}, "", "/login?error=private-token-payload&callbackUrl=https%3A%2F%2Fattacker.invalid%2Faccount");
    render(<LoginPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Sign-in could not be completed. Please try again.");
    expect(alert).not.toHaveTextContent("private-token-payload");
    expect(alert).not.toHaveTextContent("attacker.invalid");
  });

  it("clears a previous failure only when a new consent-gated attempt starts", async () => {
    window.history.replaceState({}, "", "/login?error=Callback");
    render(<LoginPage />);
    await screen.findByRole("alert");
    const button = await screen.findByRole("button", { name: "Continue with Google" });

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(button);

    await waitFor(() => expect(mocked.signIn).toHaveBeenCalledWith("google", { callbackUrl: "/dashboard" }));
    expect(mocked.prepareConsent).toHaveBeenCalledOnce();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(mocked.push).not.toHaveBeenCalled();
  });
  it("does not describe a backend outage as an incorrect password", async () => {
    mocked.signIn.mockResolvedValue({ ok: false, error: "AUTH_UNAVAILABLE", status: 503, url: null });
    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "person@example.invalid" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "synthetic-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/sign-in.*unavailable/i);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/invalid email or password/i);
    expect(mocked.push).not.toHaveBeenCalled();
  });

  it("offers explicit forgotten-password recovery without automatically starting Google", async () => {
    render(<LoginPage />);
    expect(screen.getByRole("link", { name: "Forgot your HireWiz password?" })).toHaveAttribute("href", "/account-recovery");
    expect(mocked.signIn).not.toHaveBeenCalled();
    expect(mocked.prepareConsent).not.toHaveBeenCalled();
  });

  it("returns a consented ordinary Google sign-in to the fixed recovery destination", async () => {
    mocked.postLoginPath.mockReturnValue("/account-recovery");
    render(<LoginPage />);
    const button = await screen.findByRole("button", { name: "Continue with Google" });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(button);
    await waitFor(() => expect(mocked.signIn).toHaveBeenCalledWith("google", { callbackUrl: "/account-recovery" }));
    expect(mocked.prepareConsent).toHaveBeenCalledOnce();
  });

});

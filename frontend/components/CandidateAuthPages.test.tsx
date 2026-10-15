import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const state = vi.hoisted(() => ({ status: "loading" }));
const guard = vi.hoisted(() => vi.fn());
vi.mock("next-auth/react", () => ({useSession:()=>({status:state.status}),getProviders:async()=>({}),signIn:vi.fn()}));
vi.mock("next/navigation",()=>({useRouter:()=>({push:vi.fn()})}));
vi.mock("../lib/analytics",()=>({trackEvent:vi.fn(), resetAnalyticsIdentity:vi.fn()}));
vi.mock("../lib/candidateAuthClient",()=>({guardedSignOut:guard,candidateRegistration:vi.fn(),postLoginPath:()=>"/dashboard"}));
import LoginPage from "../app/login/page";
import LogoutPage from "../app/logout/page";
import RegisterPage from "../app/register/page";
beforeEach(()=>{state.status="loading";guard.mockReset();vi.stubGlobal("fetch",vi.fn(async()=>Response.json({fresh_registration:false,legacy_enrollment:false})));});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
describe("candidate auth owner resolution and explicit recovery",()=>{
 it("keeps controlled login fields disabled until session owner resolution",async()=>{
  const view=render(<LoginPage/>);expect(screen.getByLabelText("Email address")).toBeDisabled();
  state.status="authenticated";view.rerender(<LoginPage/>);
  await waitFor(()=>expect(screen.getByLabelText("Email address")).toBeEnabled());
 });
 it("preserves ordinary signup and explicitly labels unavailable native enrollment",async()=>{
  state.status="unauthenticated";render(<RegisterPage/>);
  expect(await screen.findByText(/Browser companion enrollment is not available/)).toBeInTheDocument();
  expect(screen.queryByRole("checkbox",{name:/Create a password account eligible/})).toBeNull();
  expect(screen.getByRole("button",{name:"Create account"})).toBeDisabled();
 });
 it("never starts logout while owner resolution can remount it, then unknown requires explicit retry",async()=>{
  guard.mockRejectedValue(new Error("synthetic-unavailable"));
  const view=render(<LogoutPage/>);expect(guard).not.toHaveBeenCalled();
  state.status="authenticated";view.rerender(<LogoutPage/>);
  await screen.findByRole("alert");expect(guard).toHaveBeenCalledTimes(1);
  view.rerender(<LogoutPage/>);expect(guard).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button",{name:"Retry sign-out"}));
  await waitFor(()=>expect(guard).toHaveBeenCalledTimes(2));
 });
});

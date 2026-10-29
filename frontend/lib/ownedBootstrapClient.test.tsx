import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ownerProfileQueryOptions } from "./ownerProfile";
import { creditAvailabilityOptions, creditWalletOptions } from "./unifiedCreditClient";
import { featureDecisionsOptions, fetchOwnedProfile, fetchOwnedWallet, ownedBootstrapKey } from "./ownedBootstrapClient";
import { ownedBootstrapFixture } from "./fixtures/ownedBootstrap";

const get = vi.hoisted(() => vi.fn());
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), apiGet: get }));
let client: QueryClient;
function Readers() {
  const profile = useQuery(ownerProfileQueryOptions()), features = useQuery(featureDecisionsOptions());
  const availability = useQuery(creditAvailabilityOptions()), wallet = useQuery(creditWalletOptions());
  const cache = useQueryClient(), [imperative, setImperative] = useState("");
  useEffect(() => { let active = true;
    void Promise.all([fetchOwnedProfile(cache), fetchOwnedWallet(cache)]).then(([profile, wallet]) => {
      if (active) setImperative(`${profile.email}/${wallet.available_credits}`);
    }).catch(() => {});
    return () => { active = false; };
  }, [cache]);
  return <><p>Profile:{profile.data?.email}</p><p>Feature:{String(features.data?.features.career_workspace?.enabled)}</p>
    <p>Availability:{String(availability.data?.catalog_enabled)}</p><p>Wallet:{wallet.data?.available_credits ?? "Unavailable"}</p>
    <p>Wallet error:{String(wallet.isError)}</p><p>Imperative:{imperative}</p></>;
}
beforeEach(() => { get.mockReset(); client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); });
afterEach(() => { cleanup(); client.clear(); });
const tree = () => <QueryClientProvider client={client}><Readers /></QueryClientProvider>;

it("deduplicates four projections and both imperative callers into one actual owned HTTP read", async () => {
  get.mockResolvedValue(ownedBootstrapFixture()); render(tree());
  await screen.findByText("Imperative:owner@example.com/0");
  expect(screen.getByText("Profile:owner@example.com")).toBeVisible();
  expect(screen.getByText("Feature:true")).toBeVisible(); expect(screen.getByText("Availability:false")).toBeVisible();
  expect(get.mock.calls.map(([path]) => path)).toEqual(["/auth/bootstrap"]);
  expect(get.mock.calls[0][1]).toBeInstanceOf(AbortSignal);
});

it("isolates a wallet component failure while retaining the last successfully read balance and live profile", async () => {
  const wallet = { schema_version: "unified-credits-v1", server_time: "2026-10-29T00:00:00Z", available_credits: 17, reserved_credits: 0, debt_credits: 0, lots: [] };
  get.mockResolvedValueOnce(ownedBootstrapFixture({ wallet })).mockResolvedValue(ownedBootstrapFixture({ wallet: null }));
  render(tree()); await screen.findByText("Wallet:17");
  await act(async () => { await client.invalidateQueries({ queryKey: ownedBootstrapKey }); });
  await screen.findByText("Wallet error:true");
  expect(screen.getByText("Wallet:17")).toBeVisible(); expect(screen.getByText("Profile:owner@example.com")).toBeVisible();
  expect(get).toHaveBeenCalledTimes(2);
});

it("keeps an initially unknown balance unavailable rather than manufacturing zero", async () => {
  get.mockResolvedValue(ownedBootstrapFixture({ wallet: null })); render(tree());
  await screen.findByText("Wallet error:true"); expect(screen.getByText("Wallet:Unavailable")).toBeVisible();
  expect(screen.queryByText("Wallet:0")).toBeNull(); expect(screen.getByText("Profile:owner@example.com")).toBeVisible();
});

it("aborts an unused shared observer read on unmount and does not retry verification refusal", async () => {
  get.mockImplementation(() => new Promise(() => {}));
  function Observer() { useQuery(ownerProfileQueryOptions()); return null; }
  const view = render(<QueryClientProvider client={client}><Observer /></QueryClientProvider>);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1)); const signal = get.mock.calls[0][1] as AbortSignal;
  view.unmount(); expect(signal.aborted).toBe(true);
});

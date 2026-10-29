import React from "react";
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UnifiedCreditCatalog } from "../../lib/unifiedCredits";

const catalogRead = vi.hoisted(() => vi.fn());
vi.mock("../../lib/unifiedCreditCatalog", () => ({ getPublicUnifiedCreditCatalog: catalogRead }));
vi.mock("../../components/ui/PageHeader", () => ({ default: ({ subtitle }: { subtitle: string }) => <header>{subtitle}</header> }));
vi.mock("../../components/ui/FadeIn", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("../../components/TrackEventOnView", () => ({ default: () => null }));
vi.mock("../../components/billing/PublicUnifiedCreditPacks", () => ({ default: () => <section>Verified credit packs</section> }));

import PricingPage from "./page";

const catalog = { checkout_enabled: false, products: [] } as unknown as UnifiedCreditCatalog;
afterEach(() => { cleanup(); catalogRead.mockReset(); });

describe("public pricing during the pilot", () => {
  it("does not promise passes or payment while the rollout is closed", async () => {
    catalogRead.mockResolvedValue({ status: "not_open", catalog: null });
    render(await PricingPage());
    expect(screen.getByText("Credit passes are not open yet")).toBeInTheDocument();
    expect(screen.getByText(/No payment can start now/)).toBeInTheDocument();
    expect(screen.queryByText(/Three finite 30-day passes/)).not.toBeInTheDocument();
    expect(screen.queryByText("Verified credit packs")).not.toBeInTheDocument();
  });

  it("does not invent prices when the live catalog cannot be verified", async () => {
    catalogRead.mockResolvedValue({ status: "unavailable", catalog: null });
    render(await PricingPage());
    expect(screen.getByText("Current prices cannot be verified")).toBeInTheDocument();
    expect(screen.getByText(/No checkout can start without verified current prices/)).toBeInTheDocument();
    expect(screen.queryByText("Verified credit packs")).not.toBeInTheDocument();
  });

  it("labels verified packs as a preview while checkout is off", async () => {
    catalogRead.mockResolvedValue({ status: "ready", catalog });
    render(await PricingPage());
    expect(screen.getByText("Verified credit packs")).toBeInTheDocument();
    expect(screen.getByText(/Purchases are not open during this pilot/)).toBeInTheDocument();
    expect(screen.queryByText(/Three finite 30-day passes/)).not.toBeInTheDocument();
  });
});

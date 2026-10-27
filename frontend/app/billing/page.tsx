"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { LoadingBlock } from "../../components/ui/LoadingBlock";
import { UnifiedBillingPanel } from "../../components/billing/UnifiedBillingPanel";
import { LegacyBillingAccount } from "../../components/billing/LegacyBillingAccount";

function BillingContent() {
  const search = useSearchParams();
  const [legacyPending, setLegacyPending] = useState(true);
  return <main className="app-page"><div className="page-container space-y-10">
    <header className="border-b border-border pb-7"><p className="eyebrow">Account billing</p>
      <h1 className="font-display mt-2 text-4xl sm:text-5xl">Credits for your next move</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">One credit wallet for eligible services. Review a finite 30-day pass and every service charge before you pay. No automatic renewal.</p></header>
    <UnifiedBillingPanel legacyPending={legacyPending} requestedSku={search.get("sku")} />
    <LegacyBillingAccount onPendingChange={setLegacyPending} />
  </div></main>;
}

export default function BillingPage() {
  return <Suspense fallback={<main className="app-page"><div className="page-container"><LoadingBlock rows={5} /></div></main>}><BillingContent /></Suspense>;
}

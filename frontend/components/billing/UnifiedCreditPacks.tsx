"use client";

import { useId } from "react";
import Link from "next/link";
import {
  formatCreditDate, formatCreditPrice, packNeedsPriceRefresh, purchaseQuoteExpired, showReferencePrice,
  type EligibleCreditService, type UnifiedCreditCatalog, type UnifiedCreditPack, type UnifiedPassSku, type UnifiedPurchaseQuote,
} from "../../lib/unifiedCredits";
import { Button } from "../ui/Button";
import { creditOperationLabel, isCreditOperation, serviceLimitLabel } from "../../lib/unifiedServiceQuotes";
import { useServerClock } from "../../lib/useServerClock";

type PacksProps = {
  catalog: UnifiedCreditCatalog;
  onReview: (sku: UnifiedPassSku) => void;
  onRefresh?: () => void;
  reviewingSku?: UnifiedPassSku | null;
  disabled?: boolean;
};

function PackPrice({ pack, now }: { pack: UnifiedCreditPack; now: number }) {
  const campaign = pack.campaign;
  const currentOffer = campaign?.status === "active" && !packNeedsPriceRefresh(pack, now);
  const reference = showReferencePrice(pack, now);
  return <div className="mt-5 space-y-2">
    {currentOffer ? <p className="text-xs font-semibold text-primary">{reference ? "Limited offer" : "Introductory price"}</p> : null}
    <p className="flex flex-wrap items-baseline gap-3">
      {reference && campaign ? <span className="text-lg text-muted-foreground"><span className="sr-only">Standard price </span><s>{formatCreditPrice(campaign.standard_amount_minor)}</s></span> : null}
      <span className="text-4xl font-semibold text-foreground">{formatCreditPrice(pack.amount_minor)}</span>
    </p>
    <p className="text-xs text-muted-foreground">INR total including applicable taxes · one-time payment</p>
    {currentOffer && campaign ? <p className="text-xs leading-5 text-muted-foreground">
      Offer ends <time dateTime={campaign.ends_at}>{formatCreditDate(campaign.ends_at, campaign.timezone)}</time> ({campaign.timezone}).
      {reference ? <> Save {formatCreditPrice(campaign.standard_amount_minor - pack.amount_minor)}.</> : null}
    </p> : null}
  </div>;
}

function EligibleServices({ services }: { services: EligibleCreditService[] }) {
  return <div className="space-y-3"><h3 className="font-semibold">Services included in this offer</h3>
    {!services.length ? <p className="text-sm text-muted-foreground">No qualified paid service is currently listed. Purchasing is unavailable.</p> : <ul className="space-y-3">{services.map((service) => <li key={service.operation} className="rounded-lg border border-border p-4 text-sm">
      <p className="font-semibold">{isCreditOperation(service.operation) ? creditOperationLabel(service.operation) : service.operation.replaceAll("_", " ")} · {service.credits} {service.credits === 1 ? "credit" : "credits"} per approved operation</p>
      <p className="mt-2 leading-6 text-muted-foreground">{service.deliverable}</p>
      {service.source_formats.length ? <p className="mt-2 text-xs">Supported source formats: {service.source_formats.join(", ").toUpperCase()}</p> : null}
      <ul className="mt-2 space-y-1 text-xs text-muted-foreground">{Object.entries(service.supported_limits).map(([key, value]) => <li key={key}>{serviceLimitLabel(key)}: {value.toLocaleString("en-IN")}</li>)}</ul>
    </li>)}</ul>}
    <p className="text-xs leading-6 text-muted-foreground">Only these qualified services are listed for this offer. Each use still requires its exact input and charge review and is subject to available capacity.</p>
  </div>;
}

export function UnifiedCreditPacks({ catalog, onReview, onRefresh, reviewingSku = null, disabled = false }: PacksProps) {
  const id = useId();
  const boundaries = catalog.products.flatMap((pack) => pack.campaign ? [Date.parse(pack.campaign.starts_at), Date.parse(pack.campaign.ends_at)] : []);
  const now = useServerClock(catalog.server_time, boundaries);
  const stale = catalog.products.some((pack) => packNeedsPriceRefresh(pack, now));
  return <section aria-labelledby={`${id}-heading`} className="space-y-5">
    <div><h2 id={`${id}-heading`} className="font-display text-2xl">One credit balance for your next move</h2>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">Choose a finite 30-day pass. Review each service charge before use. No subscription or automatic renewal.</p></div>
    {!catalog.products.length ? <p role="status" className="rounded-xl border border-border p-5">No credit pass is available right now.</p> : <div className="grid gap-5 md:grid-cols-3">
      {catalog.products.map((pack) => {
        const needsRefresh = packNeedsPriceRefresh(pack, now);
        const canReview = catalog.eligible_services.length > 0 && catalog.checkout_enabled && pack.purchase_enabled && !needsRefresh && !disabled && reviewingSku === null;
        return <article key={pack.sku} className="surface-panel flex min-w-0 flex-col p-5 sm:p-6" aria-labelledby={`${id}-${pack.sku}`}>
          <h3 id={`${id}-${pack.sku}`} className="font-display text-2xl">{pack.name}</h3>
          <PackPrice pack={pack} now={now} />
          <ul className="my-6 space-y-3 text-sm leading-6">
            <li><strong>{pack.credits} credits</strong> in one balance</li>
            <li>Valid for {pack.duration_days} days from verified payment</li>
            <li>Use for eligible HireWiz services at their reviewed prices</li>
            <li>Existing purchases keep their original terms</li>
          </ul>
          {needsRefresh ? <p className="mb-4 text-xs leading-5 text-muted-foreground">This price needs refreshing because the offer period changed.</p> : null}
          <Button type="button" className="mt-auto min-h-11 w-full" disabled={!canReview} onClick={() => onReview(pack.sku)}>
            {reviewingSku === pack.sku ? "Preparing quote…" : `Review ${pack.name} pass`}
          </Button>
        </article>;
      })}
    </div>}
    <EligibleServices services={catalog.eligible_services} />
    {stale ? <div role="status" className="text-sm leading-6"><p>The offer period changed. Refresh prices before reviewing a new purchase.</p>
      {onRefresh ? <Button type="button" variant="secondary" className="mt-3 min-h-11" disabled={disabled || reviewingSku !== null} onClick={onRefresh}>Refresh prices</Button> : null}</div> : null}
    {!catalog.checkout_enabled ? <p role="status" className="text-sm text-muted-foreground">Credit purchases are not available yet. No payment will start.</p> : null}
    <p className="text-xs leading-6 text-muted-foreground">The next screen shows the exact total and minimum credit-expiry timestamp. Read our <Link href="/terms" prefetch={false} className="underline">Terms</Link> and <Link href="/refund" prefetch={false} className="underline">Refund policy</Link> before purchasing. A pass does not guarantee job selection or enable unsupported application actions.</p>
  </section>;
}

type QuoteProps = {
  quote: UnifiedPurchaseQuote;
  onAccept: (quoteId: string) => void;
  onRefresh: () => void;
  busy?: boolean;
  checkoutEnabled: boolean;
  disabled?: boolean;
  disabledReason?: string;
};

export function UnifiedPurchaseQuoteReview({ quote, onAccept, onRefresh, busy = false, checkoutEnabled, disabled = false, disabledReason }: QuoteProps) {
  const id = useId();
  const now = useServerClock(quote.server_time, [Date.parse(quote.expires_at)]);
  const expired = purchaseQuoteExpired(quote, now);
  return <section aria-labelledby={`${id}-heading`} className="surface-panel space-y-5 p-5 sm:p-7">
    <div><h2 id={`${id}-heading`} className="font-display text-2xl">Review your {quote.name} pass</h2>
      <p className="mt-2 text-sm leading-6">{quote.credits} credits · {quote.duration_days} days · no automatic renewal</p></div>
    <dl className="space-y-4 text-sm">
      <div><dt className="text-muted-foreground">Total payable including applicable taxes</dt><dd className="mt-1 text-3xl font-semibold">{formatCreditPrice(quote.amount_minor)}</dd></div>
      <div><dt className="text-muted-foreground">Minimum guaranteed credit expiry</dt><dd className="mt-1"><time dateTime={quote.pass_expires_at}>{formatCreditDate(quote.pass_expires_at)}</time> (Asia/Kolkata)</dd></div>
      <div><dt className="text-muted-foreground">Quoted price valid until</dt><dd className="mt-1"><time dateTime={quote.expires_at}>{formatCreditDate(quote.expires_at)}</time> (Asia/Kolkata)</dd></div>
    </dl>
    <EligibleServices services={quote.eligible_services} />
    <p className="text-xs leading-6 text-muted-foreground">This quote holds your price for up to 15 minutes, even if the offer ends. Credits last at least 30 full days after verified payment; a later confirmation can extend the displayed minimum expiry. Your receipt shows the final expiry.</p>
    <p className="text-xs leading-6 text-muted-foreground">Failed or incomplete services restore credits usable for at least seven days after restoration, or receive the applicable monetary remedy. Existing purchase rights remain protected. <Link href="/refund" prefetch={false} className="underline">Review refund terms</Link>.</p>
    {expired ? <p role="alert" className="text-sm text-coral">This quote expired. Get a fresh quote and review its price before paying.</p> : null}
    {!checkoutEnabled ? <p role="status" className="text-sm text-muted-foreground">Checkout is currently unavailable. No payment will start.</p> : null}
    {disabled && disabledReason ? <p role="status" className="text-sm text-muted-foreground">{disabledReason}</p> : null}
    <div className="flex flex-col gap-3 sm:flex-row">
      <Button type="button" className="min-h-11" disabled={expired || busy || !checkoutEnabled || disabled || quote.eligible_services.length === 0} onClick={() => onAccept(quote.quote_id)}>{busy ? "Opening secure checkout…" : `Continue with ${formatCreditPrice(quote.amount_minor)} payment`}</Button>
      <Button type="button" variant="secondary" className="min-h-11" disabled={busy} onClick={onRefresh}>Get a fresh quote</Button>
    </div>
    <p className="break-all text-xs text-muted-foreground">Quote reference: {quote.quote_id} · Terms version: {quote.terms_version}</p>
  </section>;
}

export const UNIFIED_CREDITS_SCHEMA = "unified-credits-v1" as const;
export const UNIFIED_PASS_SKUS = ["starter_pass", "growth_pass", "scale_pass"] as const;
export type UnifiedPassSku = typeof UNIFIED_PASS_SKUS[number];

export type CreditCampaign = {
  id: string;
  version: string;
  starts_at: string;
  ends_at: string;
  timezone: string;
  status: "scheduled" | "active" | "ended";
  standard_amount_minor: number;
  reference_price_verified: boolean;
};

export type UnifiedCreditPack = {
  sku: UnifiedPassSku;
  name: string;
  amount_minor: number;
  credits: number;
  duration_days: 30;
  auto_renews: false;
  purchase_enabled: boolean;
  terms_version: string;
  campaign: CreditCampaign | null;
};

export type EligibleCreditService = { operation: string; credits: number; deliverable: string;
  supported_limits: Record<string, number>; source_formats: string[]; route_version: string };

export type UnifiedCreditCatalog = {
  schema_version: typeof UNIFIED_CREDITS_SCHEMA;
  catalog_version: string;
  server_time: string;
  market: "IN";
  currency: "INR";
  checkout_enabled: boolean;
  products: UnifiedCreditPack[];
  eligible_services: EligibleCreditService[];
};

export type UnifiedPurchaseQuote = {
  schema_version: typeof UNIFIED_CREDITS_SCHEMA;
  quote_id: string;
  sku: UnifiedPassSku;
  name: string;
  catalog_version: string;
  terms_version: string;
  amount_minor: number;
  currency: "INR";
  credits: number;
  duration_days: 30;
  auto_renews: false;
  created_at: string;
  expires_at: string;
  pass_expires_at: string;
  eligible_services: EligibleCreditService[];
  server_time: string;
  campaign: CreditCampaign | null;
};

export type UnifiedCreditWallet = {
  schema_version: typeof UNIFIED_CREDITS_SCHEMA;
  server_time: string;
  available_credits: number;
  debt_credits: number;
  reserved_credits: number;
  lots: { grant_id: string; remaining_credits: number; expires_at: string | null; funding_kind: string }[];
};

export type UnifiedCreditHistory = {
  schema_version: typeof UNIFIED_CREDITS_SCHEMA;
  items: { id: string; event_type: string; amount: number; balance_after: number; created_at: string }[];
  next_cursor: string | null;
};

export type UnifiedCreditOrder = {
  schema_version: typeof UNIFIED_CREDITS_SCHEMA;
  order_id: string;
  provider_order_id: string | null;
  status: "pending" | "paid" | "refunded" | "failed";
  payment_status: string;
  fulfilled: boolean;
  checkout_available: boolean;
  amount_minor: number;
  refunded_amount_minor: number;
  currency: "INR";
  sku: UnifiedPassSku;
  created_at: string;
  paid_at: string | null;
  pass_expires_at: string | null;
  accepted_quote: UnifiedPurchaseQuote;
};

export type UnifiedCheckoutOrder = {
  schema_version: typeof UNIFIED_CREDITS_SCHEMA;
  order_id: string;
  provider: "razorpay";
  provider_order_id: string;
  key_id: string;
  amount_minor: number;
  currency: "INR";
  name: string;
  description: string;
  accepted_quote: UnifiedPurchaseQuote;
  prefill: { email: string } | null;
};

const DAY_MS = 86_400_000;
const QUOTE_LIFETIME_MS = 15 * 60_000;
const money = new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", minimumFractionDigits: 0, maximumFractionDigits: 2,
});

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function nonempty(value: unknown, max = 160): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

function positiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function nonnegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function isUnifiedPassSku(value: unknown): value is UnifiedPassSku {
  return typeof value === "string" && UNIFIED_PASS_SKUS.includes(value as UnifiedPassSku);
}

// Require an explicit timezone; browser-local date parsing must never determine
// which price or expiry the candidate sees.
export function creditTimestamp(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!parts) return null;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, offsetHour, offsetMinute] = parts;
  const year = Number(yearText), month = Number(monthText), day = Number(dayText);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (month < 1 || month > 12 || day < 1 || day > days[month - 1]
    || Number(hourText) > 23 || Number(minuteText) > 59 || Number(secondText) > 59
    || (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function isTimezone(value: unknown): value is string {
  if (!nonempty(value, 80)) return false;
  try { new Intl.DateTimeFormat("en-IN", { timeZone: value }); return true; } catch { return false; }
}

export function campaignStatusAt(campaign: CreditCampaign, now: number): CreditCampaign["status"] {
  if (now < Date.parse(campaign.starts_at)) return "scheduled";
  return now < Date.parse(campaign.ends_at) ? "active" : "ended";
}

function parseCampaign(value: unknown, at: number, amount: number): CreditCampaign | null | undefined {
  if (value === null) return null;
  if (!record(value) || !nonempty(value.id) || !nonempty(value.version)
    || !isTimezone(value.timezone) || !positiveInteger(value.standard_amount_minor)
    || !["scheduled", "active", "ended"].includes(String(value.status))
    || (value.reference_price_verified !== undefined && typeof value.reference_price_verified !== "boolean")) return undefined;
  const starts = creditTimestamp(value.starts_at), ends = creditTimestamp(value.ends_at);
  if (starts === null || ends === null || ends <= starts) return undefined;
  const campaign: CreditCampaign = {
    id: value.id, version: value.version, starts_at: value.starts_at as string,
    ends_at: value.ends_at as string, timezone: value.timezone,
    status: value.status as CreditCampaign["status"], standard_amount_minor: value.standard_amount_minor,
    reference_price_verified: value.reference_price_verified === true,
  };
  if (campaign.status !== campaignStatusAt(campaign, at)) return undefined;
  if (campaign.status === "active" && campaign.standard_amount_minor < amount) return undefined;
  if (campaign.reference_price_verified && (campaign.status !== "active" || campaign.standard_amount_minor <= amount)) return undefined;
  return campaign;
}

function parsePack(value: unknown, serverTime: number): UnifiedCreditPack | null {
  if (!record(value) || !isUnifiedPassSku(value.sku) || !nonempty(value.name, 80)
    || !positiveInteger(value.amount_minor) || !positiveInteger(value.credits)
    || value.duration_days !== 30 || value.auto_renews !== false
    || typeof value.purchase_enabled !== "boolean" || !nonempty(value.terms_version)) return null;
  const campaign = parseCampaign(value.campaign, serverTime, value.amount_minor);
  if (campaign === undefined) return null;
  return { sku: value.sku, name: value.name, amount_minor: value.amount_minor, credits: value.credits,
    duration_days: 30, auto_renews: false, purchase_enabled: value.purchase_enabled,
    terms_version: value.terms_version, campaign };
}

function parseEligibleServices(value: unknown): EligibleCreditService[] | null {
  if (value === undefined) return []; // Historical snapshots remain readable, never payable without offers.
  if (!Array.isArray(value) || value.length > 20) return null;
  const services: EligibleCreditService[] = [];
  for (const item of value) {
    if (!record(item) || !nonempty(item.operation, 80) || !/^[a-z][a-z0-9_]*$/.test(item.operation)
      || !positiveInteger(item.credits) || !nonempty(item.deliverable, 500) || !nonempty(item.route_version, 120)
      || !record(item.supported_limits) || Object.keys(item.supported_limits).length > 12
      || Object.entries(item.supported_limits).some(([key, limit]) => !/^[a-z][a-z0-9_]{0,79}$/.test(key) || ["constructor", "prototype"].includes(key) || !nonnegativeInteger(limit))
      || !Array.isArray(item.source_formats) || item.source_formats.length > 8 || !item.source_formats.every((format) => nonempty(format, 32))
      || new Set(item.source_formats).size !== item.source_formats.length) return null;
    services.push({ operation: item.operation, credits: item.credits, deliverable: item.deliverable,
      supported_limits: { ...item.supported_limits } as Record<string, number>, source_formats: [...item.source_formats], route_version: item.route_version });
  }
  return new Set(services.map((item) => item.operation)).size === services.length ? services : null;
}

export function parseUnifiedCreditCatalog(value: unknown): UnifiedCreditCatalog | null {
  if (!record(value) || value.schema_version !== UNIFIED_CREDITS_SCHEMA || !nonempty(value.catalog_version)
    || value.market !== "IN" || value.currency !== "INR" || typeof value.checkout_enabled !== "boolean"
    || !Array.isArray(value.products) || value.products.length > UNIFIED_PASS_SKUS.length) return null;
  const serverTime = creditTimestamp(value.server_time);
  if (serverTime === null) return null;
  const eligible = parseEligibleServices(value.eligible_services);
  if (!eligible || (value.checkout_enabled && eligible.length === 0)) return null;
  const products = value.products.map((product) => parsePack(product, serverTime));
  if (products.some((product) => product === null)) return null;
  const valid = products as UnifiedCreditPack[];
  if (new Set(valid.map((product) => product.sku)).size !== valid.length) return null;
  return { schema_version: UNIFIED_CREDITS_SCHEMA, catalog_version: value.catalog_version,
    server_time: value.server_time as string, market: "IN", currency: "INR",
    checkout_enabled: value.checkout_enabled, products: valid, eligible_services: eligible };
}

export function parseUnifiedPurchaseQuote(value: unknown): UnifiedPurchaseQuote | null {
  if (!record(value) || value.schema_version !== UNIFIED_CREDITS_SCHEMA || !nonempty(value.quote_id)
    || !isUnifiedPassSku(value.sku) || !nonempty(value.name, 80) || !nonempty(value.catalog_version)
    || !nonempty(value.terms_version) || !positiveInteger(value.amount_minor) || value.currency !== "INR"
    || !positiveInteger(value.credits) || value.duration_days !== 30 || value.auto_renews !== false) return null;
  const created = creditTimestamp(value.created_at), expires = creditTimestamp(value.expires_at);
  const passExpires = creditTimestamp(value.pass_expires_at), serverTime = creditTimestamp(value.server_time);
  if (created === null || expires === null || passExpires === null || serverTime === null
    || created > serverTime || expires <= created || expires - created > QUOTE_LIFETIME_MS
    || passExpires < created + 30 * DAY_MS) return null;
  // The original campaign can end during an accepted quote. Its snapshot is
  // validated at creation, while quote expiry uses the latest server clock.
  const campaign = parseCampaign(value.campaign, created, value.amount_minor);
  const eligible = parseEligibleServices(value.eligible_services);
  if (campaign === undefined || !eligible) return null;
  return { schema_version: UNIFIED_CREDITS_SCHEMA, quote_id: value.quote_id, sku: value.sku, name: value.name,
    catalog_version: value.catalog_version, terms_version: value.terms_version,
    amount_minor: value.amount_minor, currency: "INR", credits: value.credits,
    duration_days: 30, auto_renews: false, created_at: value.created_at as string,
    expires_at: value.expires_at as string, pass_expires_at: value.pass_expires_at as string,
    server_time: value.server_time as string, campaign, eligible_services: eligible };
}

export function formatCreditPrice(amountMinor: number): string {
  return money.format(amountMinor / 100);
}

export function formatCreditDate(value: string, timezone = "Asia/Kolkata"): string {
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: timezone }).format(new Date(value));
}

export function packNeedsPriceRefresh(pack: UnifiedCreditPack, now: number): boolean {
  return pack.campaign !== null && campaignStatusAt(pack.campaign, now) !== pack.campaign.status;
}

export function showReferencePrice(pack: UnifiedCreditPack, now: number): boolean {
  return pack.campaign?.reference_price_verified === true && pack.campaign.status === "active"
    && campaignStatusAt(pack.campaign, now) === "active" && pack.campaign.standard_amount_minor > pack.amount_minor;
}

export function purchaseQuoteExpired(quote: UnifiedPurchaseQuote, now: number): boolean {
  return now >= Date.parse(quote.expires_at);
}

export function sameUnifiedPurchaseQuote(actual: UnifiedPurchaseQuote, accepted: UnifiedPurchaseQuote): boolean {
  // server_time is refreshed on replay and is not part of the accepted purchase.
  const { server_time: actualTime, ...actualTerms } = actual;
  const { server_time: acceptedTime, ...acceptedTerms } = accepted;
  void actualTime; void acceptedTime;
  return JSON.stringify(actualTerms) === JSON.stringify(acceptedTerms);
}

export function parseUnifiedCreditWallet(value: unknown): UnifiedCreditWallet | null {
  if (!record(value) || value.schema_version !== UNIFIED_CREDITS_SCHEMA || creditTimestamp(value.server_time) === null
    || !nonnegativeInteger(value.available_credits) || !nonnegativeInteger(value.debt_credits)
    || !nonnegativeInteger(value.reserved_credits) || !Array.isArray(value.lots) || value.lots.length > 5_000) return null;
  const lots: UnifiedCreditWallet["lots"] = [];
  for (const lot of value.lots) {
    if (!record(lot) || !nonempty(lot.grant_id) || !nonnegativeInteger(lot.remaining_credits)
      || !nonempty(lot.funding_kind, 40) || (lot.expires_at !== null && creditTimestamp(lot.expires_at) === null)) return null;
    lots.push({ grant_id: lot.grant_id, remaining_credits: lot.remaining_credits,
      expires_at: lot.expires_at as string | null, funding_kind: lot.funding_kind });
  }
  if (new Set(lots.map((lot) => lot.grant_id)).size !== lots.length) return null;
  return { schema_version: UNIFIED_CREDITS_SCHEMA, server_time: value.server_time as string,
    available_credits: value.available_credits, debt_credits: value.debt_credits, reserved_credits: value.reserved_credits, lots };
}

export function parseUnifiedCreditHistory(value: unknown): UnifiedCreditHistory | null {
  if (!record(value) || value.schema_version !== UNIFIED_CREDITS_SCHEMA || !Array.isArray(value.items) || value.items.length > 50
    || (value.next_cursor !== undefined && value.next_cursor !== null && !nonempty(value.next_cursor, 1024))) return null;
  const items: UnifiedCreditHistory["items"] = [];
  for (const item of value.items) {
    if (!record(item) || !nonempty(item.id) || !nonempty(item.event_type, 80)
      || typeof item.amount !== "number" || !Number.isSafeInteger(item.amount) || !nonnegativeInteger(item.balance_after)
      || creditTimestamp(item.created_at) === null) return null;
    items.push({ id: item.id, event_type: item.event_type, amount: item.amount,
      balance_after: item.balance_after, created_at: item.created_at as string });
  }
  if (new Set(items.map((item) => item.id)).size !== items.length) return null;
  return { schema_version: UNIFIED_CREDITS_SCHEMA, items, next_cursor: value.next_cursor as string | null ?? null };
}

export function parseUnifiedCreditOrder(value: unknown): UnifiedCreditOrder | null {
  if (!record(value) || value.schema_version !== UNIFIED_CREDITS_SCHEMA || !nonempty(value.order_id)
    || !["pending", "paid", "refunded", "failed"].includes(String(value.status)) || !nonempty(value.payment_status)
    || typeof value.fulfilled !== "boolean" || typeof value.checkout_available !== "boolean"
    || !positiveInteger(value.amount_minor) || !nonnegativeInteger(value.refunded_amount_minor)
    || value.refunded_amount_minor > value.amount_minor || value.currency !== "INR" || !isUnifiedPassSku(value.sku)
    || creditTimestamp(value.created_at) === null
    || (value.provider_order_id != null && !nonempty(value.provider_order_id))
    || (value.paid_at != null && creditTimestamp(value.paid_at) === null)
    || (value.pass_expires_at != null && creditTimestamp(value.pass_expires_at) === null)) return null;
  const quote = parseUnifiedPurchaseQuote(value.accepted_quote);
  if (!quote || quote.amount_minor !== value.amount_minor || quote.sku !== value.sku
    || (value.fulfilled && value.status !== "paid")
    || (value.checkout_available && (value.status !== "pending" || !value.provider_order_id))
    || (typeof value.pass_expires_at === "string" && Date.parse(value.pass_expires_at) < Date.parse(quote.pass_expires_at))) return null;
  return { schema_version: UNIFIED_CREDITS_SCHEMA, order_id: value.order_id,
    provider_order_id: value.provider_order_id as string | null ?? null,
    status: value.status as UnifiedCreditOrder["status"], payment_status: value.payment_status,
    fulfilled: value.fulfilled, checkout_available: value.checkout_available, amount_minor: value.amount_minor,
    refunded_amount_minor: value.refunded_amount_minor, currency: "INR", sku: value.sku,
    created_at: value.created_at as string, paid_at: value.paid_at as string | null ?? null,
    pass_expires_at: value.pass_expires_at as string | null ?? null, accepted_quote: quote };
}

export function parseUnifiedCheckoutOrder(value: unknown, accepted: UnifiedPurchaseQuote): UnifiedCheckoutOrder | null {
  if (accepted.eligible_services.length === 0) return null;
  if (!record(value) || value.schema_version !== UNIFIED_CREDITS_SCHEMA || value.provider !== "razorpay"
    || !nonempty(value.order_id) || !nonempty(value.provider_order_id) || !nonempty(value.key_id)
    || value.amount_minor !== accepted.amount_minor || value.currency !== "INR" || !nonempty(value.name)
    || !nonempty(value.description, 1000)) return null;
  const quote = parseUnifiedPurchaseQuote(value.accepted_quote);
  if (!quote || !sameUnifiedPurchaseQuote(quote, accepted)) return null;
  if (value.prefill != null && (!record(value.prefill) || !nonempty(value.prefill.email, 320))) return null;
  return { schema_version: UNIFIED_CREDITS_SCHEMA, order_id: value.order_id, provider: "razorpay",
    provider_order_id: value.provider_order_id, key_id: value.key_id, amount_minor: accepted.amount_minor, currency: "INR",
    name: value.name, description: value.description, accepted_quote: quote,
    prefill: value.prefill ? { email: (value.prefill as { email: string }).email } : null };
}

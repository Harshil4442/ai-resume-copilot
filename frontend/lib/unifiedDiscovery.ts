import { z } from "zod";
import type { components } from "./generated/api";
import { parseUnifiedServiceQuote } from "./unifiedServiceQuotes";

export type DiscoveryRequest = components["schemas"]["DiscoveryRequest"];
export type DiscoveryCreate = components["schemas"]["DiscoveryCreate"];
const count = z.number().int().min(0).max(5);
const batchSchema = z.object({
  id: z.string().regex(/^[A-Za-z0-9_.:-]{1,120}$/),
  status: z.enum(["preparing", "completed", "failed", "cancelled"]),
  billing_currency: z.literal("credits"), desired_count: count.min(1), delivered_count: count,
  charged_credits: z.number().int().min(0).max(1), reserved_credits: z.number().int().min(0).max(1),
  refunded_credits: z.number().int().min(0).max(1), partial_batch: z.boolean(), maximum_batch_size: z.literal(5),
  scope: z.record(z.string(), z.unknown()), items: z.array(z.record(z.string(), z.unknown())).max(5),
}).superRefine((batch, ctx) => {
  const completed = batch.status === "completed";
  if (batch.delivered_count > batch.desired_count || batch.items.length !== batch.delivered_count
    || batch.partial_batch !== (completed && batch.delivered_count > 0 && batch.delivered_count < batch.desired_count)
    || (!completed && batch.delivered_count !== 0)
    || batch.charged_credits !== (completed && batch.delivered_count > 0 ? 1 : 0)
    || (batch.status !== "preparing" && batch.reserved_credits !== 0)
    || (batch.charged_credits && batch.refunded_credits)) ctx.addIssue({ code: "custom", message: "Inconsistent discovery accounting" });
});
export type DiscoveryBatch = z.infer<typeof batchSchema>;
export function parseDiscoveryBatch(value: unknown): DiscoveryBatch | null {
  const result = batchSchema.safeParse(value); return result.success ? result.data : null;
}
export function parseDiscoveryQuote(value: unknown, requested: number) {
  const quote = parseUnifiedServiceQuote(value);
  return quote && quote.operation === "job_discovery_batch" && quote.mode === "native" && quote.credits === 1
    && quote.supported_limits.maximum_results >= requested && quote.supported_limits.maximum_results <= 5 ? quote : null;
}
export function isUnifiedDiscovery(scope: unknown): boolean {
  return !!scope && typeof scope === "object" && "billing_currency" in scope && scope.billing_currency === "credits"
    && "price_quote" in scope && !!scope.price_quote && typeof scope.price_quote === "object"
    && "pricing_unit" in scope.price_quote && scope.price_quote.pricing_unit === "completed_batch";
}

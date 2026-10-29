import { z } from "zod";
import type { components } from "./generated/api";

const digest = z.string().regex(/^[a-f0-9]{64}$/);
const reviewId = z.string().regex(/^[a-z][a-z0-9-]{7,47}$/);
const owned = z.object({
  status: z.enum(["NOT_OPTED_IN", "OPTED_IN"]), review_id: reviewId, review_sha256: digest,
  notice: z.string().min(40).max(2000), notice_sha256: digest,
  expires_at: z.string().datetime({ offset: true }), runtime_sha256: digest,
  sequence: z.number().int().min(0).max(2 ** 31 - 1), can_opt_in: z.boolean(), execution_flag_enabled: z.boolean(), accepted_work_survives_revocation: z.literal(true),
}).strict();
const enrollment = z.union([owned, z.object({ status: z.literal("NOT_AVAILABLE") }).strict()]);
export type CanaryEnrollment = z.infer<typeof enrollment>;
export type OwnedCanaryEnrollment = z.infer<typeof owned>;
export type CanaryChoice = components["schemas"]["CanaryChoiceRequest"];
export function parseCanaryEnrollment(value: unknown): CanaryEnrollment {
  const parsed = enrollment.safeParse(value);
  if (!parsed.success) throw new Error("Participation status could not be verified.");
  return parsed.data;
}
export function enrollmentPath(review?: Pick<OwnedCanaryEnrollment, "review_id" | "review_sha256">) {
  return "/v1/credits/canary-enrollment" + (review ? `?${new URLSearchParams({ review_id: review.review_id, review_sha256: review.review_sha256 })}` : "");
}

/** Exact BFF policy; selectors are never owner identity or participation authority. */
export function validCanarySelectors(query: URLSearchParams): boolean {
  const keys = [...query.keys()];
  return keys.length === 0 || (keys.length === 2 && query.getAll("review_id").length === 1
    && query.getAll("review_sha256").length === 1 && reviewId.safeParse(query.get("review_id")).success
    && digest.safeParse(query.get("review_sha256")).success);
}
const choice = z.object({ review_id: reviewId, review_sha256: digest, notice_sha256: digest,
  choice: z.enum(["opt_in", "revoke"]), expected_sequence: z.number().int().min(0).max(2 ** 31 - 2), confirmed_notice: z.boolean().optional(),
  idempotency_key: z.string().min(8).max(160),
}).strict().refine((value) => value.choice === "revoke" || value.confirmed_notice === true);
export function validCanaryChoice(value: unknown): boolean { return choice.safeParse(value).success; }

import { z } from "zod";
const text = z.string().min(1), texts = z.array(text);
export const enrichmentResult = z.object({ resume_id: z.number().int().positive(), skills: texts, enrichment_state: z.literal("completed") });
export const bulletResult = z.object({ rewritten_bullets: texts.min(1), summary: text,
  evidence_links: z.array(z.object({ original_text: text, replacement_text: text, evidence_ids: texts.min(1) })).min(1) });
export const answerResult = z.object({ answer: text, confidence: z.enum(["high", "medium", "low"]), suggested_followups: texts,
  sources: texts, mode: z.enum(["direct", "enhanced", "unavailable"]), provenance: text });
export const learningResult = z.object({ match_id: z.number().int().positive(), job_title: z.string(), company: z.string(), current_score: z.number(), readiness_summary: text,
  missing_hiring_signals: z.array(z.object({ signal: text, why_it_matters: text, severity: text })),
  learning_priorities: z.array(z.object({ skill: text, priority: text, current_status: text, reason: text, expected_outcome: text,
    resources: z.array(z.object({ title: text, platform: text, url: z.string().nullable().optional(), skill: text, level: z.string().nullable().optional() })) })),
  project_recommendations: z.array(z.object({ title: text, covers_gaps: texts, description: text, implementation_steps: texts,
    resume_bullets: texts, interview_talking_points: texts })), timeline: z.array(z.object({ phase: text, focus: text, deliverable: text })),
  generated_by: text, provenance: z.string().optional(), warnings: texts.optional() });
export function parseResult<T>(schema: z.ZodType<T>) { return (value: unknown): T | null => { const result = schema.safeParse(value); return result.success ? result.data : null; }; }
export function safeResourceHref(value: string | null | undefined) {
  if (!value) return undefined;
  try { const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password ? url.href : undefined; } catch { return undefined; }
}
export type EnrichmentResult = z.infer<typeof enrichmentResult>;

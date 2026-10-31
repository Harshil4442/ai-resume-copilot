/** Sensitive recovery surfaces never enter analytics or error URL collection. */
export function isPasswordResetPath(path: string) {
  return ["/forgot-password", "/reset-password", "/account-recovery", "/privacy-erasure", "/privacy-erasure/confirm"].includes(path.replace(/\/$/, ""))
    || /^\/api\/(?:auth\/)?(?:password-reset|privacy-erasure)(?:\/|$)/.test(path);
}

export function isPasswordResetUrl(value: unknown) {
  if (typeof value !== "string") return false;
  try { return isPasswordResetPath(new URL(value.replace(/^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /, ""), "https://redacted.invalid").pathname); }
  catch { return false; }
}

/** Also stops an already loaded Google tag during an in-app navigation. */
export function setResetGoogleExclusion(sensitive: boolean,
  measurementId = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID?.trim() || process.env.NEXT_PUBLIC_GA_ID?.trim()) {
  if (typeof window !== "undefined" && measurementId) {
    (window as unknown as Record<string, unknown>)[`ga-disable-${measurementId}`] = sensitive;
  }
}

export function resetTelemetry(value: { request?: { url?: string }; transaction?: string;
  data?: Record<string, unknown>; breadcrumbs?: Array<{ data?: Record<string, unknown> }> }) {
  return (typeof window !== "undefined" && isPasswordResetPath(window.location.pathname))
    || isPasswordResetUrl(value.request?.url) || isPasswordResetUrl(value.transaction)
    || isPasswordResetUrl(value.data?.url) || isPasswordResetUrl(value.data?.from) || isPasswordResetUrl(value.data?.to)
    || Boolean(value.breadcrumbs?.some((row) => isPasswordResetUrl(row.data?.url)
      || isPasswordResetUrl(row.data?.from) || isPasswordResetUrl(row.data?.to)));
}

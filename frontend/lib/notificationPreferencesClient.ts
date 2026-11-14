/** Exact, non-retrying notification transport. Never reflect remote error bodies. */
export type NotificationPreference = {
  weekly_digest: boolean;
  generation: number;
  policy_version: string;
  next_digest_at: string | null;
  projection_current: boolean;
};

export class NotificationRequestError extends Error {
  constructor(public readonly status: number | null) {
    super("Notification settings could not be confirmed.");
    this.name = "NotificationRequestError";
  }
}

function preference(value: unknown): NotificationPreference {
  if (!value || typeof value !== "object") throw new NotificationRequestError(null);
  const row = value as Record<string, unknown>;
  if (typeof row.weekly_digest !== "boolean" || typeof row.projection_current !== "boolean"
    || !Number.isSafeInteger(row.generation) || (row.generation as number) < 0
    || typeof row.policy_version !== "string" || !/^[A-Za-z0-9._:-]{1,80}$/.test(row.policy_version)
    || !(row.next_digest_at === null || (typeof row.next_digest_at === "string"
      && row.next_digest_at.length <= 64 && Number.isFinite(Date.parse(row.next_digest_at))))) {
    throw new NotificationRequestError(null);
  }
  return { weekly_digest: row.weekly_digest, generation: row.generation as number,
    policy_version: row.policy_version, next_digest_at: row.next_digest_at as string | null,
    projection_current: row.projection_current };
}

async function request(path: string, signal: AbortSignal, method = "GET", body?: unknown) {
  const response = await fetch(path, { method, cache: "no-store", credentials: "same-origin",
    redirect: "error", referrerPolicy: "no-referrer",
    signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]),
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });
  if (!response.ok) throw new NotificationRequestError(response.status);
  try { return preference(await response.json()); }
  catch { throw new NotificationRequestError(null); }
}

export function readNotificationPreference(signal: AbortSignal) {
  return request("/api/backend/v1/notifications/preferences", signal);
}

export async function changeNotificationPreference(current: NotificationPreference, enabled: boolean, signal: AbortSignal) {
  const updated = await request("/api/backend/v1/notifications/preferences", signal, "PUT", {
    expected_generation: current.generation, weekly_digest: enabled, explicit_confirmation: true,
  });
  if (updated.weekly_digest !== enabled || updated.generation !== current.generation + 1) {
    throw new NotificationRequestError(null);
  }
  return updated;
}

export async function unsubscribeNotifications(token: string, signal: AbortSignal) {
  const result = await request("/api/backend/public/notifications/unsubscribe", signal, "POST", { token });
  if (result.weekly_digest !== false) throw new NotificationRequestError(null);
  return result;
}

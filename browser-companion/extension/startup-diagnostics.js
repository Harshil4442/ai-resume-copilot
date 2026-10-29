// Local startup presentation only. Never inspect, repair or replace device state.
export const STARTUP_STATUS_TIMEOUT_MS = 60000;
const PREFIX = "HW-COMPANION-START-";
const KEY_PHASES = new Set(["OPEN-UNAVAILABLE", "OPEN-BLOCKED", "OPEN-ERROR", "OPEN-TIMEOUT", "TX-UNAVAILABLE", "TX-ABORT", "TX-ERROR", "TX-TIMEOUT", "MISSING-RETAINED", "IDENTITY-SHAPE", "KEY-TYPE", "KEY-ALGORITHM", "KEY-USAGES", "PUBLIC-SHAPE", "FINGERPRINT", "PUBLIC-IMPORT", "PROOF-SIGN", "PROOF-VERIFY", "CREATE", "OPTIONS"]);
const KEY_HELP = "This browser's saved device identity could not be verified. Keep the extension installed and do not clear its storage.";
const REASONS = Object.freeze({
  CONFIG: "The installed companion could not verify its fixed configuration. Keep this extension installed and inspect its error details.",
  KEY: KEY_HELP,
  ...Object.fromEntries([...KEY_PHASES].map((phase) => [`KEY-${phase}`, KEY_HELP])),
  CHECKPOINT: "The saved browser connection needs inspection. Keep the extension and its saved connection unchanged.",
  STORAGE: "Chrome could not access the saved connection state. Keep the extension installed and do not clear its storage.",
  WORKER: "The companion's background worker did not respond. Inspect the extension's error details without reinstalling it.",
  TIMEOUT: "The initial connection check timed out. Keep this extension installed; no request was automatically repeated.",
  UNKNOWN: "Startup could not be verified. Keep this extension installed and inspect its error details before choosing a recovery action.",
});
const MESSAGES = new Map([
  ["Reviewed browser connection configuration is unavailable", "CONFIG"],
  ["Reviewed production pairing configuration is unavailable", "CONFIG"],
  ["Reviewed assistance configuration is unavailable", "CONFIG"],
  ["Device key storage is unavailable", "KEY"],
  ["Stored connection state needs inspection; starting again is blocked", "CHECKPOINT"],
  ["The saved connection belongs to a different retained device key", "CHECKPOINT"],
  ["Connection checkpoint is invalid", "CHECKPOINT"],
  ["Stored assistance state needs inspection. No field action is enabled.", "CHECKPOINT"],
  ["Retained native evidence needs inspection.", "CHECKPOINT"],
  ["Connection storage is unavailable", "STORAGE"],
  ["Companion worker unavailable. Inspect the portal before retrying.", "WORKER"],
  ["Could not establish connection. Receiving end does not exist.", "WORKER"],
  ["The message port closed before a response was received.", "WORKER"],
  ["A listener indicated an asynchronous response by returning true, but the message channel closed before a response was received", "WORKER"],
]);
class StartupFailure extends Error {
  constructor(reason) { super("Companion startup could not be verified"); this.reason = reason; }
}
function reasonFor(failure) {
  if (failure instanceof StartupFailure) return failure.reason;
  try { return typeof failure?.message === "string" ? MESSAGES.get(failure.message) ?? "UNKNOWN" : "UNKNOWN"; }
  catch { return "UNKNOWN"; }
}

export async function startupStatus(sendMessage) {
  let timer;
  try {
    // Bound presentation only: do not cancel, repeat or mutate the worker's work.
    // A late status result has no path back into the popup's action rendering.
    const result = await Promise.race([
      Promise.resolve().then(() => sendMessage({ type: "status" })),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new StartupFailure("TIMEOUT")), STARTUP_STATUS_TIMEOUT_MS); }),
    ]);
    if (result?.ok !== true) {
      const phase = result?.error === "Device key storage is unavailable" && KEY_PHASES.has(result.device_phase) ? result.device_phase : null;
      const reason = phase ? `KEY-${phase}` : result?.error == null ? "WORKER" : MESSAGES.get(result.error) ?? "UNKNOWN";
      throw new StartupFailure(reason);
    }
    return result.value;
  } catch (failure) { throw new StartupFailure(reasonFor(failure)); }
  finally { clearTimeout(timer); }
}

export function startupDiagnostic(failure, version) {
  const reason = reasonFor(failure);
  return { code: PREFIX + reason, message: REASONS[reason],
    version: typeof version === "string" && /^[0-9]{1,5}(?:\.[0-9]{1,5}){0,3}$/.test(version) ? version : "unavailable" };
}

export function showStartupFailure(document, failure, version) {
  const diagnostic = startupDiagnostic(failure, version);
  const mode = document.getElementById("mode"), alert = document.getElementById("error");
  if (mode) mode.textContent = "Browser connection unavailable. No application action is enabled.";
  for (const control of document.querySelectorAll("button, input, select, textarea")) control.disabled = true;
  for (const id of ["fixture-connection", "fixture-prepare", "native-connection", "assistance", "review"]) {
    const region = document.getElementById(id); if (region) region.hidden = true;
  }
  if (alert) {
    alert.textContent = `${diagnostic.message}\nInstalled version: ${diagnostic.version}\nSupport code: ${diagnostic.code}`;
    alert.hidden = false;
  }
}

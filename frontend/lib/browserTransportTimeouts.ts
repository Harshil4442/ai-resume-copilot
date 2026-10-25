// Transport budgets surround the native operation's 30-second processing bound.
// They do not extend signed-request freshness, review expiry or authorization.
export const BROWSER_BACKEND_TIMEOUT_MS = 35_000;
export const BROWSER_CLIENT_TIMEOUT_MS = 40_000;

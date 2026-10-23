# Password recovery by email

Forgot password is a public email recovery flow. It does not require an existing session or Google verification, and it does not enroll an account for the browser companion or alter Google account links.

## User journey

1. Open **Forgot your HireWiz password?** on the sign-in page and enter the email registered with HireWiz.
2. Show the same confirmation for registered and unknown addresses. If the account exists and is eligible, send a single-use link to that stored address.
3. Open the link within 15 minutes, enter and confirm a new password, and explicitly submit the change.
4. Confirm success and send a separate password-changed notice. End old sessions and direct the user through ordinary sign-in. Do not automatically sign in.

An expired, superseded, changed-owner, or used link cannot reset a password. Request a new link. A merely opened link does not consume it, so email scanners cannot burn the token. An uncertain native credential transition must not be replayed; display the recovery/support path.

## API and privacy boundary

The website exposes `POST /api/password-reset/request` and `POST /api/password-reset/confirm`. Both require same-origin requests, the existing signed CSRF cookie/token, bounded sensitive JSON and the existing private website-to-API ingress. No login is required. The backend paths are `/api/auth/password-reset/request` and `/api/auth/password-reset/confirm`.

Request fields: `email`. A valid request returns HTTP 202 with `RESET_LINK_REQUESTED`, including unknown addresses and account cooldowns. Global delivery configuration unavailability returns `RESET_UNAVAILABLE`. The account lookup, token creation and targeted delivery run after the generic response.

Confirm fields: `token`, `new_password`. Success returns `PASSWORD_RESET`. Invalid or expired proofs return `RESET_LINK_INVALID`; uncertain protected-state outcomes return `RESET_UNAVAILABLE`. Passwords use the existing 10–128 character policy and server-side password hashing.

Reset links use a fixed HTTPS HireWiz origin and a URL fragment. The frontend removes the fragment before telemetry starts and holds the proof only in memory. Reset pages are private/no-store/noindex, use `no-referrer`, and exclude analytics and external script collection. The proof is sent only in the explicit confirmation POST. Do not log tokens, full reset URLs, passwords or credential hashes.

## State and delivery

Schema 21 adds a dedicated reset-token table and a legacy credential epoch. Token records retain only SHA-256 proof digests, expiry, current mailbox/credential/revision binding and consumption state. The 32-byte URL token combines a 16-byte cryptographic random nonce with a 16-byte purpose-separated server-secret MAC. The MAC binds the reset ID, account, mailbox, current credential/native revision, legacy epoch and exact expiry; a SQL writer cannot mint a valid proof by replacing its stored digest. Links expire after 15 minutes and requests have a 60-second account cooldown. Public endpoints also have IP rate limits. A new accepted request supersedes older pending links without changing the account password.

An authenticated-encrypted outbox message carries the delivery proof until email delivery; its key is purpose-derived from the existing strong server secret, with recipient, reset ID and expiry bound as authenticated data. Purge the delivery ciphertext after send or terminal failure. Security emails operate independently of optional lifecycle/marketing mail. Use only the targeted security outbox item for immediate delivery, with the same provider idempotency key on retry.

For legacy accounts, password change and credential-epoch advancement commit atomically. Bearers from before the change stop working, while normal Google sign-in remains available. No native enrollment is created by recovery. Enrolled accounts retain the existing independent credential generation and session-denial mechanism. Their recovery mailbox is bound immutably to the original protected account after genuine enrollment, verified password sign-in, or matching fresh Google sign-in. SQL mailbox changes cannot retarget recovery. Existing enrolled accounts without a protected mailbox binding must first use a verified existing sign-in method or support; there is no silent SQL backfill. Preserve the original MAC-verified reset proof and account binding across the native update. One original transition must be acknowledged before success. Correctly signed stale native cookies can be cleared only after independent current-account denial/high-water verification; they cannot authenticate or create a new session.

## Operations and rollout

Use a dedicated verified sending subdomain, `auth.hirewizhq.com`, and a domain-scoped sending-only Resend key. Runtime configuration is `RESEND_API_KEY` (Secret Manager reference) and `EMAIL_FROM` (verified sender). Do not expose the provider key to the browser or add it to public frontend configuration. Keep receiving and open/click tracking disabled for reset mail. Existing Google mailbox MX records remain unchanged.

The free provider plan has a daily limit; configure and verify delivery before enabling the public flow. A provider acceptance receipt is not proof of inbox delivery. Validate delivery to the owner-controlled associated mailbox, one successful reset, old-session rejection, and invalid/expired/replayed-link refusal. Use synthetic accounts for automated credential changes; the human owner submits their own new password.

Deploy schema 21 with the disjoint additive migration/rollout contract. Preserve existing rows, account links, financial records, role permissions and the independent native authority. An application rollback after the first legacy reset must retain credential-epoch enforcement; a pre-21 API image would accept revoked legacy bearers. Prefer an epoch-aware rollback or close authentication until a reviewed forward fix. Apply the reviewed source image after the schema upgrade, configure the scoped email secret on the API, and verify both the public form and actual email delivery before reporting this feature live.

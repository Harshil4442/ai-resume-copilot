# Synthetic Razorpay-like form scaffold

This hand-authored **local test fixture only** uses the public review dated 9 October
2026 in `docs/delivery/RAZORPAY_BROWSER_PILOT_2026-10-09.md`. It contains no employer
description, branding, vendor JavaScript or real candidate content. Public control
IDs/labels/options model one observed form, not a universal Greenhouse adapter.

The server binds an ephemeral `127.0.0.1` port. Ordinary Chromium tests abort every
request outside that exact origin and disable service workers. No extension is loaded,
no device is paired, no authority is started and no production account is authenticated.
Observed real processor URLs are descriptive policy strings only, never fetch targets.
All callback/file/submit recorders are local test operations and store synthetic data.

The form preserves seven optional assisted text inputs, ARIA required state, 255-character
limits, `tel` Phone, required custom comboboxes (including candidate-chosen Gender),
repeatable employment/month/year/current-role controls, and manual file/text alternatives.
Country options are an explicitly synthetic two-option subset; month widgets and date
constraints are hand-authored simulations, not proof of the unexercised live behavior.
There are more than eight required controls. Full-form completion is never claimed.

The adapter recognizes only the exact loopback origin/path, top frame and synthetic
marker/tenant/opening. It refuses sensitive/manual inputs, challenges, changed form or
manual values, changed recipient policy and unexpected before-values. Inspection emits
no events. Filling uses a short-lived, one-use **unauthenticated in-memory synthetic
gate**, irreversibly consumed before mutation. This is a DOM decision scaffold, not v2
integration, authentication, consent validity, a recovery-safe journal or replay proof
across module reload/browser restart. Page-declared digests/markers/policy are not trusted
production identity or verification of arbitrary page JavaScript/egress.

The email blur recorder models disclosure before final submission. Manual file selection
immediately sends synthetic bytes to its local recorder. File selection/upload, manual
resume text, comboboxes, employment and final submission are never adapter actions. A
selected file pauses all assistance because the scaffold has no byte-identity verifier;
filename/size/count metadata cannot authorize continuation. Perform this manual attachment
after optional assisted filling in the fixture. Synthetic callback failure is observable
and never silently retried or treated as absence of earlier disclosure. A
final manual test click would record only a synthetic event, never an employer receipt.
Cancellation cannot recall recorded callbacks. Completion says `filled_only`; no applied
state, credit charge, refund, permission grant or employer-use permission is created.

Production manifest/config/package/authority files are untouched. This fixture is not
served by HireWiz, not enabled by a flag and not included in a shipping extension build.
Production pairing/independent authority, source-use permission, exact reviewed recipient
policy, trusted byte/DOM verification and browser lifecycle integration remain absent.

Run separately from existing scripts using Node 24 and the existing lockfile:

```sh
npm ci --ignore-scripts
npm run check
node --test tests/razorpay-fixture.test.js
node --test tests/razorpay-browser.test.js
```

Browser tests require the existing Playwright Chromium installation. Set
`RAZORPAY_FIXTURE_SCREENSHOT_DIR` to an absolute temporary directory for desktop/mobile
screenshots. Existing `npm test` and `npm run test:browser` entry points remain unchanged;
neither unexpectedly loads this new browser test.

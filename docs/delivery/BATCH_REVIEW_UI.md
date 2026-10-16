# Exact batch review UI foundation

Local integration evidence recorded on 8 October 2026. This UI is a foundation for complete applications on permissioned API routes. It does not enable employer submission, expand source coverage, or prove a live employer receipt. Automatic submission remains disabled for the controlled release; the current manual sources keep individual employer handoffs.

The candidate selects a fixed list of complete saved applications. The UI creates an immutable quote with their exact package digests, explicit fill/upload/submit permissions and a maximum service credit budget. Creating the quote approves and submits nothing. The review shows each employer destination, job, original/custom/approved-tailored file choice, native file preview or DOCX download, every answer (including employer metadata), consent choice, authorized action and saved per-job price. Optional tailoring keeps its separate Workspace quote; Premium and AI analysis units do not replace service credits.

Each job must be explicitly marked reviewed after its exact file is available. DOCX review requires downloading the file for native document review. Approval covers only the enumerated packages; queueing requires a separate click. Batch members cannot use the individual application's submit button. Changing a package requires fresh review. Expired, stale, incomplete, duplicate-opening or withdrawn-source packages cannot be approved or queued. Legacy packages without a current price and admission quote fail eligibility. The service rechecks current policy and available credits; displayed limits are ceilings, not remaining capacity.

Per-job status separates queued, submitting, employer-confirmed, safely failed, cancelled and unknown outcomes. Unknown sends are not repeated and retain their admission and credit holds until independently resolved. Cancellation stops remaining work and claims no recall of disclosed data or an in-flight send. Polling makes one batch status GET every two seconds while any job is queued/submitting; individual application details refresh only on reported status transitions, approval/queue/cancel actions or explicit refresh. Unknown and terminal outcomes stop automatic polling.

Interrupted quote retries reuse the same payload-bound idempotency key. Approval and queue requests bind the same sealed batch digest and refresh authoritative status after interruptions. The existing session owner boundary replaces the query cache and page-local state before another owner renders. This flow adds no persistent candidate data to local storage. Approved or queued batches reopen through `application.batch_id`; an unapproved quote has an exact bookmarkable review link.

## Local verification

- Generated frontend API types match the integrated backend OpenAPI; the backend integrator confirmed its final regeneration was byte-identical.
- Full frontend ESLint and TypeScript checks passed, all 55 unit tests passed, and the local production build passed using synthetic test authentication configuration.
- The complete browser suite passed 190/190 cases: 155 existing cases plus 35 new batch cases across 320, 390, 768, 1024 and 1440 px.
- A subsequent presentation-only cleanup shortened the dialog title and moved raw hashes/versions and candidate limits into accessible disclosures. Fresh production build, focused ESLint and TypeScript checks passed; the final 35/35 batch cases passed. The 155 existing cases were not repeated after this presentation cleanup.
- Batch fixtures cover exact prices despite a changed catalog price, explicit maximum budget, download/review gates, separate approval and queueing, reopening an approved batch, bounded polling, late confirmed/unknown transitions, retained holds on cancellation, stale/expired packages, canonical duplicates, withdrawn source permissions, interrupted quote idempotency and account switching.
- Final batch cases report no runtime/console errors, horizontal overflow or automated WCAG 2 A/AA violations. Keyboard activation, focus, reduced motion and native disclosure controls are exercised. This is local browser evidence with synthetic data, not field performance, a comprehensive manual accessibility audit or a live provider proof.
- Fixtures intercept every backend request and reject unrecognized requests; non-fixture mutations and external origins are blocked. No customer login, payment, real application or employer POST occurred.

The machine-readable evidence is `docs/delivery/evidence/2026-10-08-batch-ui-local.json`. Full pre-cleanup browser output is `/tmp/hirewiz-batch-ui-20261008/results.json`; final focused output and screenshots are `/tmp/hirewiz-batch-ui-final-20261008/`.

The final screenshot set has 52 actual viewport frames covering the scrollable review and status dialogs at all five widths, plus ten expanded full-content exports for readable inspection. `expanded-full` exports temporarily expand only the scroll container; responsive assertions use the actual viewport. Representative readable exports:

- `/tmp/hirewiz-batch-ui-final-20261008/batch-review-mobile-320-expanded-full.png`
- `/tmp/hirewiz-batch-ui-final-20261008/batch-status-mobile-320-expanded-full.png`
- `/tmp/hirewiz-batch-ui-final-20261008/batch-review-mobile-390-expanded-full.png`
- `/tmp/hirewiz-batch-ui-final-20261008/batch-status-mobile-390-expanded-full.png`
- `/tmp/hirewiz-batch-ui-final-20261008/batch-review-desktop-1440-expanded-full.png`
- `/tmp/hirewiz-batch-ui-final-20261008/batch-status-desktop-1440-expanded-full.png`

## Remaining acceptance gaps

The backend has no batch-list endpoint, so unapproved quote recovery depends on retaining its exact review link. There is no approval-snapshot read endpoint for a package that later changed; the UI withholds current file/answer details when they no longer match the sealed item. It does not present changed contents as the earlier review.

The independent recovery epoch, deletion/revocation tombstone authority, restore quarantine, device protocol, verified employer permission/receipt contracts, fairness metrics and full controlled staging proof remain separate gates. Local batch fixtures do not close them. Exact-commit CI and controlled deployment/promotion evidence belong to the release owner.

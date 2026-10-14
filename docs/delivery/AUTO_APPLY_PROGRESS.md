# HireWiz development and deployment progress

Updated 14 October 2026 (India time).

This is the active reporting checklist. Only the six major development and
production deployment outcomes below count as checkpoints. Tests, regression
runs, builds, reviews and temporary diagnostic jobs are validation within an
outcome; they are never separate checklist completions.

| Major outcome | Status | Work remaining before completion |
|---|---|---|
| Official job search, ranking and user-selected job counts | In progress | Verify recent and closed jobs, matching quality, selected result counts and credit behavior across the complete user flow; measure coverage against a defined reference set |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | In progress | Verify truthful edits and complete original/custom/tailored resume selection, review, downloads and staged API integration |
| Candidate-reviewed direct company applications | Blocked | Complete the approved Razorpay/Greenhouse pilot, destination/file/answer review, form filling and reliable application outcome tracking; rejected pairing integration remains stopped |
| Paid search/application credits, profitable finite packs and payment recovery | In progress | Verify separate charging, finite limits, settlement/refund recovery and representative failure/load behavior; release through the native monetary safeguards |
| Fast responsive pages and SEO | In progress | Verify complete desktop, laptop and mobile journeys, loading performance and production SEO metadata |
| GCP/Vercel production deployment, preserved-data migration and monitoring | Pending production release | Finish release orchestration, preserve customer/financial data, establish the actual old-writer fence, migrate, stage, perform the reviewed production switch and monitor |

No major outcome is newly complete. Development and deployment are both required
before ending the goal. The existing HireWiz production database has not been
migrated and the new feature release has not been deployed. A separate Redis
connection repair is live on the existing API image.

Current development within these outcomes:

- Official discovery now has 20 reviewed employer origins, including 16 additional
  verified company-to-ATS joins. This is source implementation, with production
  seeding and complete search acceptance pending. Worldwide or 95% job coverage
  has not been established. See the [origin register](../delivery/EMPLOYER_ORIGIN_REGISTRY_2026-10-13.md).
- The DOCX rendering repair now passes all six original native acceptance
  cases on the new immutable GCP image. Clean PDF/DOCX parsing, both antivirus
  cases, styled multipage layout preservation and unsafe reflow refusal pass.
  The styled case preserves two pages, graphics and links, with zero measured
  displacement of protected text or pixels outside the edited region. The
  new image's eleven runtime files and read-only font inventory are verified;
  the six-case suite takes 131.310 seconds. Temporary Job and Execution removal
  and Build-source removal are independently verified. The exact helper,
  renderer, Dockerfile, test-image source list and policy are adopted into the
  worktree. This resolves the demonstrated native rendering defect; arbitrary
  resume equivalence, truthful tailoring, resume selection/downloads and the
  full production release remain pending. Rendering limits and document safety
  checks are unchanged. The cause of the earlier wrapper exit 137 remains
  unconfirmed. The approved diagnostic is published as
  [Cloud Run issue 573358022](https://issuetracker.google.com/issues/573358022).
  Exact scope and native bindings are recorded in the
  [document acceptance record](DOCUMENT_RENDERING_ACCEPTANCE_2026-10-14.md).
- The fresh Neon target now has three genuinely authenticated restricted roles
  and three verified numeric Secret Manager versions. The separate source-to-target
  release sequence, portable Linux profile and fresh-only native authority checks
  are implemented in source. Replacement passwords must differ from every captured
  old password and actually authenticate before restore. No customer data has
  been copied and no database or model-credential cutover has occurred. The [phase design](../delivery/FRESH_DATABASE_RELEASE_PHASES_2026-10-13.md)
  distinguishes migration from serving; the actual final writer fence and
  migration-role retirement still precede promotion. The isolated Linux rehearsal
  now passes all fifteen PostgreSQL transfer and process-cleanup cases when
  Docker runs with an init process. The same tests previously failed two
  descendant cleanup checks without that process. The migration runner must
  retain this reaping setup and the existing cleanup assertions. The concurrent
  native freshness check now returns its fixed refusal on wait timeout while
  keeping the original freshness window and clearing the retirement cut.
- The local full migration executor is blocked by the standing approval-review
  restriction: its required source-integrity check reads every tracked file,
  including the stopped browser-pairing/lifecycle files. That check remains
  intact. The executor has not been run, and changing its execution environment
  or skipping those files is not an approved workaround. The separate GitHub
  builder replacement is prepared and reviewed, but no trigger or builder IAM
  changes have occurred. The reviewed source executor is now adopted into the
  managed worktree. It retains the pre-cut password bank until all replacement
  passwords pass comparison and genuine authentication, and requires a native
  trigger hold and complete Build drain before retiring the old builder.
  Source adoption does not resolve the execution restriction or prove cutover.
- Google sign-in is restored on the live site. The failed Redis Cloud hostname
  was replaced by a user-authorized free Upstash resource through Vercel
  Marketplace (Cleveland, 500,000 monthly commands, eviction and automatic paid
  upgrade disabled). Only the existing API account has a resource-scoped grant
  on the new Redis secret. The same API image passed a staged login check before
  production traffic moved to the repaired revision; real Google sign-in and a
  populated dashboard are verified. The customer database and workers were not
  changed. Clearer frontend errors and the narrowly scoped backend 503 response
  are committed separately, awaiting the full feature release.
- Finite paid packs and separate search/application rates are implemented in
  source. Search admission now requires the displayed pricing version, unit
  price and exact requested-count maximum. Stale quotes refuse before new
  reservations; interrupted requests retain their original quote and key.
  Billing now has owner-scoped, paginated service history with actual held,
  charged and released amounts, separate from analysis history. These source
  changes have focused verification; production journeys are still pending.
  Checkout source now validates the reviewed pack total and both credit
  allowances before creating an order. Stable request identities recover
  original orders across interrupted responses and captured-payment races;
  resuming checkout requires another explicit review of its original terms.
  Unknown provider creation keeps its reservation, blocks replacement attempts
  regardless of age, and offers free status checks and support reconciliation.
  Positive recovery now has an explicit owner-scoped action using bounded
  provider GETs outside database locks. It validates the original receipt,
  account mode/key and immutable terms, then re-locks and applies actual
  capture/refund facts with separate API-proof provenance. Empty, incomplete
  or conflicting proof keeps the original attempt held. Only a verified
  unattempted created order can resume after another review; the server also
  refuses paused-state checkout requests. Original credential rotation,
  negative absence proof, paid-unfulfilled and unsupported outcomes remain
  support cases. Twenty-six isolated backend checks and twenty checkout UI
  checks pass; native provider acceptance and full monetary release remain
  pending. These checks do not complete the billing outcome.
  Native financial continuity and production activation remain pending. Reviewed
  expense and model cost policies are now stored as exact numeric Secret Manager
  versions with native checksum/hash readback. No runtime access grant or policy
  activation occurred; their original expiry and pricing limits are preserved.
- Public SEO source now includes explicit social metadata, a branded preview
  image and a sitemap without invented dates. The earlier Vercel preview was
  built from commit `ad7ddd6`. The homepage was checked at actual 1512×805 and
  390×844 viewports, and pricing at 1366×900 and 390×844. Public metadata,
  keyboard/mobile navigation and page overflow checks passed. The wrapped score
  denominator is corrected in source. Billing history now uses valid definition
  list markup; the affected desktop/mobile accessibility cases pass with their
  original assertions. The subsequent `045d9db` preview also passed its actual
  390×844 homepage check: the score stays on one line, page width equals scroll
  width, and the keyboard menu opens and closes. Production release remains
  pending.
  Complete user journeys, measured loading performance and production verification
  remain pending. Its pricing page awaits the server-owned billing catalog.

Automatic approval review rejected the browser-pairing/lifecycle integration as
a possible cybersecurity risk. That work remains stopped; it is not bypassed by
the other development or deployment work.

The previous time estimates have not held and are not a current completion ETA.
When one of these same six outcomes actually completes, report its original
estimate if recorded, measured elapsed time, identifiable waits and the evidence
for completion. Do not add smaller checkpoints to create apparent progress.

The [archived implementation and validation history](archive/AUTO_APPLY_VALIDATION_HISTORY_THROUGH_2026-10-13.md)
preserves earlier results and timing without counting them as delivery completions.

The latest completed source run (`6d6723b`, GitHub Actions `38506666828`)
passed all five jobs, including the backend integration and native OpenAPI
byte check. It includes positive payment reconciliation and the two repaired
purchase fixtures. This is source validation within the existing outcomes;
it does not establish provider acceptance or production deployment.

The exact `6d6723b` Vercel preview is ready. It has not been promoted to
production, and its complete authenticated journeys are not yet verified.

The exact `e41d8d2` preview is ready. Public home and pricing pages fit 390-pixel
mobile and 1,366-pixel laptop widths without horizontal overflow; mobile
navigation and canonical metadata are verified. Pricing remains unavailable
without a usable backend catalog. One authenticated HTTP sample per page
measured 0.220 s and 0.810 s to first byte; these are preview response samples,
not browser paint, full journey or production performance proof. No validation
result here completes a major outcome or authorizes production release.

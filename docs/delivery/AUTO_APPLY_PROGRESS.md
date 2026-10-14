# HireWiz development and deployment progress

Updated 14 October 2026 (India time).

This is the active reporting checklist. Only the six major development and
production deployment outcomes below count as checkpoints. Tests, regression
runs, builds, reviews and temporary diagnostic jobs are validation within an
outcome; they are never separate checklist completions.

| Major outcome | Status | Work remaining before completion |
|---|---|---|
| Official job search, ranking and user-selected job counts | In progress | Verify recent and closed jobs, matching quality, selected result counts and credit behavior across the complete user flow; measure coverage against a defined reference set |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | In progress | Verify the GCP DOCX restart repair against original layout, verify truthful edits, and complete original/custom/tailored resume selection and downloads |
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
- Format-preserving DOCX output is still failing its two native layout cases.
  The latest exact-image GCP run passed all four parsing/antivirus cases,
  verified the ten installed runtime hashes and actual font/tool manifest,
  and completed cleanup for every case. Its owned Job, Execution and Build
  source upload are absent. A reviewed candidate permits one normal engine
  restart within the original deadline and corrects process ownership during
  cleanup. It is held until rendered layout acceptance succeeds. Earlier
  diagnostics observed exit 81 followed by a wrapper report of exit 137;
  this does not establish memory exhaustion or the latest failure's cause.
  A focused diagnostic on the reviewed ownership repair now confirms the first
  child's actual exit 81 and successful cleanup, followed by second-attempt
  admission and an exact wrapper report of exit 137. It did not observe the
  second child's exit. The bound own-cgroup view returned unavailable memory
  counters, so the cause remains unclassified. The temporary Job and Execution
  are verified absent. Rendering limits and document safety checks remain
  unchanged; repeating the same failed acceptance run alone is not progress.
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
  image and a sitemap without invented dates. The current Vercel preview is
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

The latest completed source run (`e41d8d2`, GitHub Actions `38502419603`)
passed frontend, cold-browser, companion and security/container jobs. Backend
reported two ordinary failures and 4,216 passes: the fresh-user purchase fixture
needs the new explicit quote/request identity, and the retired legacy-purchase
case needs the intended original-order rejection contract. Both fixture repairs are
included with positive payment reconciliation; the next native run must verify
them against the full integration. The public schema's
native byte check remains pending because backend tests failed first.

The exact `e41d8d2` preview is ready. Public home and pricing pages fit 390-pixel
mobile and 1,366-pixel laptop widths without horizontal overflow; mobile
navigation and canonical metadata are verified. Pricing remains unavailable
without a usable backend catalog. One authenticated HTTP sample per page
measured 0.220 s and 0.810 s to first byte; these are preview response samples,
not browser paint, full journey or production performance proof. No validation
result here completes a major outcome or authorizes production release.

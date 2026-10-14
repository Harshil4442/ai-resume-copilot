# HireWiz development and deployment progress

Updated 15 October 2026 (India time).

This is the active reporting checklist. Only the six major development and
production deployment outcomes below count as checkpoints. Tests, regression
runs, builds, reviews and temporary diagnostic jobs are validation within an
outcome; they are never separate checklist completions.

| Major outcome | Status | Work remaining before completion |
|---|---|---|
| Official job search, ranking and user-selected job counts | In progress | Verify recent and closed jobs, matching quality, selected result counts and credit behavior across the complete user flow; measure coverage against a defined reference set |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | In progress | Verify truthful edits and complete original/custom/tailored resume selection, review, downloads and staged API integration |
| Candidate-reviewed direct company applications | In progress | Complete staged acceptance and activation of the Razorpay/Greenhouse pilot, exact destination/package/answer review, text autofill and reliable outcome tracking; file upload and final submission remain candidate actions in this pilot |
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
- Earlier agent records described an approval-review rejection affecting the
  pairing/lifecycle integration. An audit of the available session records did
  not recover the original rejected call or its exact scope. The current session
  has Full Access; a permanent file-level restriction has not been established.
  Authorized source integration is proceeding; the unverified historical note
  is not treated as a standing source restriction. The complete source-integrity check runs under current Full
  Access. After reconciling generated declarations and preserving existing
  notes, it verifies all 1,023 tracked files and the 16-revision migration chain.
  The full migration executor has not run. No source files are excluded from
  that check. The separate GitHub
  builder replacement is prepared and reviewed. The actual regional GitHub
  trigger is held, with its previous build configuration preserved; replacement
  builder IAM and trigger retargeting remain pending. The reviewed source executor is now adopted into the
  managed worktree. It retains the pre-cut password bank until all replacement
  passwords pass comparison and genuine authentication, and requires a native
  trigger hold and complete Build drain before retiring the old builder.
  Source adoption does not prove cutover.
- The release configuration now includes the five accepted document worker pins
  for the API and analysis worker, the replacement runtime invoker identity, and
  Direct VPC routing. Native Compute and DNS API enablement, a dedicated network
  and its Private Google Access subnet are confirmed. The private DNS zone and
  both records are created; native metadata matches the fixed routing contract.
  Actual authenticated requests from a Cloud Run caller remain pending.
  The accepted document image is deployed on private revision
  `hirewiz-document-inspector-00005-qbw`; the previous revision is retained and
  natively verified. Its configuration and empty service IAM policy match.
  The project-policy verifier now accepts standard version-3 condition metadata
  for unrelated principals, while continuing to refuse public/worker principals
  and conditional service grants. No policy or API traffic was changed by this
  parser repair. Full caller integration and release remain pending.
  Document/network metadata checks alone cannot establish live connectivity or
  complete the tailoring or deployment outcomes. See the
  [private routing contract](DOCUMENT_PRIVATE_ROUTING.md).
- The separate LaTeX worker now passes all eight native Cloud Run cases on its
  new immutable image, including timeout cleanup and clean compilation after
  refusals. Installed runtime hashes match the reviewed source. The demonstrated
  cleanup defect was a terminated child adopted by the outer process rather
  than the request controller. A TeX-only initialized subreaper now adopts and
  reaps exact owned descendants while retaining the parent anchor and original
  cleanup deadline; the accepted DOCX launcher is unchanged. Earlier failed
  receipts remain retained. The separate private service is ready on revision
  `hirewiz-native-tex-00001-67r`, with empty initial service IAM and no customer
  processing enabled. The actual service URI and accepted image are bound into
  source. Final cohort labels, runtime invocation, private HTTP checks and the
  complete resume journey remain pending.
- Production browser-assistance integration now uses owner-scoped PostgreSQL
  approvals and requests, a device-held signing key, exact reviewed values and
  short per-field leases. The actual installed Chrome extension has exercised
  signed transport against an isolated SQL/cryptographic backend, including
  lost-response refusal without replay and missing-host-permission refusal.
  The current public Razorpay/Greenhouse form has been inspected and its five
  supported text fields mapped from current metadata. This is implementation
  and isolated integration evidence. The downloadable source package is prepared;
  the live staged user journey, production delivery and activation remain pending. Autofill alone
  is not application submission and must not settle application credits.
- The Linux release runner's authentication failure has been isolated to the
  mounted host credential-manager path. A private in-memory handoff from the
  normal host authentication clients passes the read-only diagnostic, including
  actual source database authentication. The source URI's required channel
  binding is now preserved. Supported full-runner integration and token-expiry
  handling are implemented. Actual stock Google and GitHub reads now pass inside
  Linux through that RAM handoff, using genuinely fresh host authentication.
  This diagnostic is not final full-source release admission. Three
  application queues still require a deliberate freeze and drain at cutover.
- The manual pilot now has an explicit owner-authenticated submission report.
  The candidate reviews the current saved package and reports submission; the
  first UTC timestamp is retained on repeated requests. The result is labelled
  user-reported, creates no employer receipt, verifies no actual attachment and
  charges no application fee. It stops further editing, execution and browser
  filling. Release readers allow this terminal state only with valid manual,
  zero-fee metadata and no concealed execution attempt or credit reservation.
  Terminal candidate reports no longer consume the open-application preparation
  limit; the same-opening duplicate fence is retained. The downloadable companion
  is checked against its exact source bytes, and its installed browser/SQL flow
  is now included in the release CI workflow.
  Deployment and actual user-flow acceptance remain pending.
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

Routine development is not waiting for another user permission. Remaining
release work is final cohort verification, production browser
integration and final native migration/release acceptance. The supported Linux
authentication path now passes actual read-only provider checks. Customer data has
not been migrated and the new feature release has not been promoted.

The previous time estimates have not held and are not a current completion ETA.
The demonstrated LaTeX native failures are now repaired and accepted, but this
does not establish completion of the full resume workflow or the wider goal.
Release and final verification were estimated at
2–4 active hours after the blockers are resolved; that conditional estimate
does not bound the unresolved repair work or promise a completion deadline.
When one of these same six outcomes actually completes, report its original
estimate if recorded, measured elapsed time, identifiable waits and the evidence
for completion. Do not add smaller checkpoints to create apparent progress.

The [archived implementation and validation history](archive/AUTO_APPLY_VALIDATION_HISTORY_THROUGH_2026-10-13.md)
preserves earlier results and timing without counting them as delivery completions.

The earlier source run (`6d6723b`, GitHub Actions `38506666828`)
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

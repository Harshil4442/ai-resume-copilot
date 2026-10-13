# HireWiz development and deployment progress

Updated 14 October 2026 (India time).

This is the active reporting checklist. Only the six major development and
production deployment outcomes below count as checkpoints. Tests, regression
runs, builds, reviews and temporary diagnostic jobs are validation within an
outcome; they are never separate checklist completions.

| Major outcome | Status | Work remaining before completion |
|---|---|---|
| Official job search, ranking and user-selected job counts | In progress | Verify recent and closed jobs, matching quality, selected result counts and credit behavior across the complete user flow; measure coverage against a defined reference set |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | In progress | Resolve the actual GCP document processor termination, verify original layout and truthful edits, and complete original/custom/tailored resume selection and downloads |
| Candidate-reviewed direct company applications | Blocked | Complete the approved Razorpay/Greenhouse pilot, destination/file/answer review, form filling and reliable application outcome tracking; rejected pairing integration remains stopped |
| Paid search/application credits, profitable finite packs and payment recovery | In progress | Verify separate charging, finite limits, settlement/refund recovery and representative failure/load behavior; release through the native monetary safeguards |
| Fast responsive pages and SEO | In progress | Verify complete desktop, laptop and mobile journeys, loading performance and production SEO metadata |
| GCP/Vercel production deployment, preserved-data migration and monitoring | Pending production release | Finish release orchestration, preserve customer/financial data, establish the actual old-writer fence, migrate, stage, perform the reviewed production switch and monitor |

No major outcome is newly complete. Development and deployment are both required
before ending the goal. The existing HireWiz production database and deployment
have not been switched to the new release.

Current development within these outcomes:

- Official discovery now has 20 reviewed employer origins, including 16 additional
  verified company-to-ATS joins. This is source implementation, with production
  seeding and complete search acceptance pending. Worldwide or 95% job coverage
  has not been established. See the [origin register](../delivery/EMPLOYER_ORIGIN_REGISTRY_2026-10-13.md).
- Format preservation works in the native local verification path, but the actual
  GCP layout path still terminates during DOCX rendering. A separate conversion
  reproduces the failure without antivirus scanning. The latest trace reaches
  child launch, gate authorization and selector waiting, then stops before
  output completion or cleanup. Exit status 137 is observed; memory counters
  and the termination cause remain unproved. Temporary diagnostic Jobs and
  Executions have been removed.
- The fresh Neon target now has three genuinely authenticated restricted roles
  and three verified numeric Secret Manager versions. The separate source-to-target
  release sequence, portable Linux profile and fresh-only native authority checks
  are implemented in source. Replacement passwords must differ from every captured
  old password and actually authenticate before restore. No customer data has
  been copied and no production credential cutover has occurred. The [phase design](../delivery/FRESH_DATABASE_RELEASE_PHASES_2026-10-13.md)
  distinguishes migration from serving; the actual final writer fence and
  migration-role retirement still precede promotion.
- Google and password sign-in currently fail at the shared Redis rate limiter:
  the configured Redis Cloud hostname no longer resolves, and the signed-in
  Redis Cloud account has no databases. A user-authorized free Upstash resource
  is now available through Vercel Marketplace (Cleveland, 500,000 monthly
  commands, eviction disabled). Its production GCP connection is not switched
  yet. Frontend failure messages and a narrowly scoped backend 503 response are
  implemented locally; these changes do not themselves restore login.
- Finite paid packs and separate search/application rates are implemented in
  source. Native financial continuity and production activation remain pending.
- Public SEO source now includes explicit social metadata, a branded preview
  image and a sitemap without invented dates. The current Vercel preview is
  built and its mobile navigation, overflow and social metadata have been checked.
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

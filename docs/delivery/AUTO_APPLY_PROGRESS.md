# HireWiz development and deployment progress

Updated 15 October 2026. The six outcomes below are the original reporting
checkpoints. A test run, build, review, diagnostic or partial implementation is
supporting evidence within an outcome, never a newly completed checkpoint.

| Original outcome | Status | Required before completion |
|---|---|---|
| Official job search, ranking and user-selected job counts | In progress | Activate official feeds; verify fresh/closed jobs, matching, requested counts and per-new-job charging through the complete user journey; measure coverage against a defined reference set |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | In progress | Verify truthful supported edits, review, original/custom/tailored selection and downloads through the deployed API and private document services |
| Candidate-reviewed direct company applications | In progress | Deploy ordinary candidate enrollment/pairing; verify required and conditional fields, exact reviewed resume upload, separate final-submit approval, one submission, genuine employer acceptance and unknown-outcome reconciliation |
| Paid search/application credits, profitable finite packs and payment recovery | In progress | Verify distinct balances, holds, settlement, refunds and duplicate/failure recovery in production; calibrate estimates against actual expenses and demand |
| Fast responsive pages and SEO | In progress | Verify complete laptop, desktop and mobile journeys, loading performance and production SEO metadata |
| GCP/Vercel deployment, preserved-data migration and monitoring | In progress; not promoted | Finish preserved-data migration and staged revision checks, restore deployment automation, activate private upload custody, publish the frontend and verify monitored production behavior |

**No whole outcome is newly complete.** Development and deployment must both be
verified before ending the goal. Previous estimates have been exceeded; there is
no dependable remaining completion estimate yet. Completed-outcome timing will
record the original estimate and measured duration against these same six rows.

## Latest verified deployment state

- The F7 application and operator images are built from the frozen release. The
  DOCX and LaTeX workers are deployed; their runtime invocation by the new API
  and the complete resume journey still require verification.
- The old application writers are isolated. Source ownership and observer
  credentials were transitioned, and the original database owner was removed.
  The source database remains preserved. Replacement target credentials and the
  isolated model key were issued and genuinely authenticated.
- The subsequent deployment stopped while creating the required backup, before
  customer-data transfer or traffic promotion. The read-only export account
  lacked access to Neon's existing managed authentication schema.
- That permissions defect is now resolved with an explicit read-only grant from
  the surviving provider role. The temporary administrator was removed. A real
  PostgreSQL export of the complete source, including both schemas, succeeded
  with no stderr. The diagnostic archive was discarded; it was not a production
  backup or a completed migration. The permanent source fix is documented in
  [complete read-only export](SOURCE_COMPLETE_READONLY_EXPORT_2026-10-15.md).
- Recovery is being prepared from the exact already-issued credential versions,
  original cloud audit records and original key-creation operation. It must
  freshly verify current authority and resume the remaining transfer/staging
  steps without repeating completed credential issuance.
- The four application queues, maintenance schedule, checkout and new-feature
  admissions remain held. The current production login and new user journeys
  require verification after the migration; a public HTML page or basic health
  response does not establish that those journeys work.

## Development ready for the subsequent release

- Official discovery has 20 reviewed employer origins across three ATS families.
  Production seeding and complete search acceptance are pending. Worldwide or
  95% coverage has not been established. See the
  [origin register](EMPLOYER_ORIGIN_REGISTRY_2026-10-13.md).
- Search delivers the candidate's selected count, subject to actual matches and
  explicit limits. Source pricing separates one service credit per newly
  delivered job, 20 service credits per confirmed application, and two AI units
  for optional tailoring. Holds and unknown outcomes must reconcile without
  charging duplicates or repeating a submission.
- Finite packs replace unlimited usage: ₹649 for 100 service credits and two AI
  units; ₹1,099 for 300 and six; ₹2,199 for 700 and 15. These are estimate-based
  prices awaiting production calibration, not a guarantee of profitability at
  every customer volume. See [cost policy](COST_PRICING_POLICY_2026-10-09.md).
- Ordinary candidate account enrollment, Google binding and consented device
  pairing are implemented in source. Their native authority resources and
  deployed user flow remain pending. See
  [candidate activation](NORMAL_CANDIDATE_BROWSER_ACTIVATION_2026-10-15.md).
- Razorpay/Greenhouse remains the first browser-assistance portal. The companion
  must own the approved upload bytes and correlate the actual HTTPS request and
  response with the exact approved file, destination and answers. Ordinary
  browser TLS validation is the trust boundary; the product does not claim
  certificate pinning or native raw-file introspection where Chrome omits it.
  Installed browser integration and production acceptance remain unfinished.
- Private upload buckets and signing identities exist. The restricted coordinator
  SQL identity, workload, Scheduler and effective invocation grants still need
  activation after the migration, followed by genuine custody/scan/cleanup checks.

The [requirements](../requirements/JOB_SEARCH_AND_APPLICATION.md) define the
acceptance scope. Earlier run-by-run observations are retained in the
[historical progress archive](archive/AUTO_APPLY_PROGRESS_THROUGH_F6_2026-10-15.md)
and are not additional completed checkpoints.

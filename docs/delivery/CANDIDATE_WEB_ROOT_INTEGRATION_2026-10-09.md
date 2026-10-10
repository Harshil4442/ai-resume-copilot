# Candidate website integration — 9 October 2026

Status: independently reviewed website transport and bounded private ingress integrated
and locally verified. Contention availability, protected browser connection and
production release remain open.

The candidate signs up or signs in through the actual NextAuth website, performs
owned account actions through dedicated BFF handlers, and receives public status
from the actual local SQL/native lifecycle. Credentials, bearer tokens, native
session context and the retained logout request stay on the server.

The combined V1/R2 result was first reconstructed on committed `4911d1f`. All
38 changed product paths matched the independently reviewed source before root
integration and after verification and API contract regeneration. All 881 frozen
independent artifacts, 766 author artifacts and 671 author source hashes were
verified. V1's original transport HOLD remains historical evidence; V1 was never
integrated without the reviewed R2 repair.

| Flow boundary | Result | Evidence |
| --- | --- | --- |
| Signup/login UI to NextAuth | PASS | Actual HTTPS Chromium creates separate synthetic native and ordinary accounts; retained account replacement is refused |
| Browser to dedicated account handlers | PASS | Real CSRF/origin checks, profile update, export and deletion; generic credential issuer paths remain refused |
| API to database/native lifecycle | PASS | Real PostgreSQL and native emulator enroll, verify passwords and retain logout/deletion state |
| Lifecycle response to browser | PASS | Public session and owned export contain no bearer/native context; pending registration cannot activate itself |
| Logout and stale cookie recovery | PASS | Both persisted and unpersisted uncertain outcomes preserve the cookie until the same retained denial is confirmed; an unrelated candidate remains usable |

The unchanged cold journey passes **46 checks**. Its owned SQL schema was independently
verified absent; both owned server groups stopped and its private TLS key was removed.
Native administrative UID, Storage HTTP and signing custody are explicit synthetic
fixture dependencies. This is local browser/API/data evidence, not production custody.

The frontend passes **190 tests**, lint, type checking and its production build with
a synthetic process-only build secret. Configured backend Ruff and Mypy pass; Mypy
checks 110 files. Regenerating OpenAPI preserves the reviewed contract bytes.

The initial backend run selected 2,030 cases: **1,850 passed, 180 setup errors,
zero assertion failures and zero skips**. The configured URL selected a different
local database from the fixture's required pre-existing `hirewiz_admission_test`.
After verifying that exact database read-only, only the 180 refused cases were rerun
and all passed. This verifies 2,030 unique cases across the two runs; it is not a
single all-pass whole-suite run. Both original setup-error artifacts remain retained.
An earlier host-Python preflight lacked SQLAlchemy and did not invoke pytest; the
locked project interpreter was used afterward.

The prior exact `4911d1f` [CI run](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37933390559)
passes all five jobs, including 2,002 backend cases; it predates the website patch. The exact `0993b23` run subsequently passes all
2,030 backend cases, security/container and companion jobs, but fails the frontend
responsive job (20 failures) and cold browser job. These failures are preserved in
the [follow-up integration report](PRIVATE_INGRESS_ROOT_INTEGRATION_2026-10-09.md).
Both fixture compatibility repairs pass locally; new exact-source CI remains required.

- [x] Verify the independent frozen source and combine both reviewed revisions.
- [x] Integrate the exact 38-path result and regenerate unchanged API contracts.
- [x] Verify actual account browser/API/data flow and owned cleanup.
- [x] Verify frontend/static checks and all unique local backend cases.
- [ ] Complete all five exact new-source CI jobs after the compatibility repairs.
- [x] Review and integrate bounded private ingress; verify the intended HTTP 429
  through the actual website BFF, with separate limits recorded in the follow-up report.
- [ ] Complete genuine protected browser connection and contention availability.
- [ ] Provision protected production resources, retire writers, migrate and deploy.

The independent R2 review's original sixth native signup HTTP 500, and the original
ingress review's BFF projection to HTTP 503, remain preserved. Separate integrated
backend/public projection repairs now produce fixed HTTP 429 through the actual BFF.
Earlier mixed-fault browser waits and the initial reset concurrency failure
remain open; later passing probes do not diagnose them. A separate protected-join
review was interrupted by automated review as possible cybersecurity risk and has
no final independent clearance.

No GCP/Vercel settings, traffic, payments, employer submissions or AI calls changed.
The default native factory remains unavailable. Exact hashes, preserved failures,
local results and release limits are in [the evidence record](evidence/2026-10-09-candidate-web-root-integration.json).

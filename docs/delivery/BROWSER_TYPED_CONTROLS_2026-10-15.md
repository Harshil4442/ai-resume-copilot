# Reviewed phone and dropdown controls

This work advances the existing direct-company-application checkpoint. It does
not complete automatic application submission or its deployment.

The version-two browser review adds the candidate's explicitly saved Phone and
four saved public-form single-choice fields: Current Career Stage, Gender,
Current Industry and Current Designation. The existing seven text semantics are
retained, allowing at most twelve reviewed field actions. Job-specific question
IDs and one-to-one option labels/values come from that job's saved form; IDs from
another or a closed opening are not reused. No AI call or inferred personal
answer is involved. A missing answer, including Gender, remains manual.

The candidate sees the exact file, employer destination, answers, current values
and disclosure before approval. Phone and dropdown actions carry an explicit
version and type in their signed per-field lease. A choice additionally binds
its exact visible option label. Historical version-one approvals keep their
original text-only capability. The new feature flag
`BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED` defaults to false and stops new
version-two actions when disabled.

The adapter handles the hosted ReactSelect widget, which has no named hidden
option-ID input. It checks the rendered label against the saved one-to-one
option map. Normal placeholder, selected-label, live-region and the two observed
focus-style transitions are normalized; other widget attributes and structure
remain bound. Unknown options, changed button behavior, changed conditional
questions, ownership or document drift stop the sequence. Opening a dropdown
can itself disclose state, so it occurs only within the approved field action.
Unknown outcomes are never retried automatically.

Read-only inspection of the current official Razorpay engineering opening
`4740333005` and its public question schema confirmed this shape. Country and
employment fields exist on the hosted form and are omitted from the public
question feed. They remain explicit manual requirements in this slice. The
historical example opening `4723040005` has closed. No candidate answer was
entered, selected, uploaded or submitted during those inspections.

Companion version 0.3.0 has a new deterministic source archive; the existing
0.2.0 archive is retained. Review UI and popup text describe the supported
answers and remaining manual actions. This flow still charges zero application
credits and produces no employer submission receipt. Country, employment,
attachment, CAPTCHA/login, final-submit approval, one-shot submission and real
receipt/reconciliation must be completed for the full paid application outcome.

Focused source validation covers actual SQLite/cryptographic twelve-action
leases, explicit missing sensitive answers, flag revocation, historical review
isolation, browser widget changes and candidate UI review. These do not prove a
successful real employer submission or production deployment. The actual
installed-companion regression remains version-one flow evidence; a complete
version-two installed-companion/SQL bridge case remains required.

Migration `20261015_0019` expands only the durable review cursor constraint from
seven to twelve. Its downgrade refuses to erase version-two history. Before
integration, verify the migration on disposable PostgreSQL, update schema-specific
release consumers and provide the reviewed schema18-to19 migration procedure.
The frozen production-candidate release remains schema18. Do not enable typed
controls against the old constraint or mark deployment complete from these local
checks.

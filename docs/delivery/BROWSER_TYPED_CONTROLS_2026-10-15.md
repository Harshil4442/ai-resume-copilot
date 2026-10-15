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
isolation, browser widget changes and candidate UI review. Installed Chromium
MV3 now exercises the complete twelve-action version-two SQL/crypto bridge,
including distinct approved values/labels for all four choices. The actual
extension Origin, signed per-field bindings, phone, event sequence, zero
reservations/charges and absence of a receipt are checked. Tampered signed
choice labels, unknown options, conditional changes, DOM drift and lost begin
acknowledgements stop without replay. Every HTTPS response is locally fulfilled;
the installed test uses exact test-only origin grants rather than accepting a
human permission dialog. This does not prove a real employer submission,
production authentication, live permission acceptance or deployment.

The negative installed tests exposed completion wording after an unknown or
stopped outcome. The companion now says filling stopped and requires inspection;
only a completed sequence says the approved answers were filled. The 0.3.0 source
ZIP was rebuilt twice with identical SHA-256
`088107e95a4570f829ed32292ca520c9d29fff2d64966ae8f5daa1840a40f618`
and verified byte-for-byte against its runtime source. The 0.2.0 ZIP is unchanged.

The initial local migration `20261015_0019` expanded the durable review cursor
constraint from seven to twelve. Its undeployed draft now also retains a
`review_protocol_version` marker (1–3) for the separately approved
[attachment action](BROWSER_ATTACHMENT_ASSISTANCE_2026-10-15.md). It backfills
existing version-two payloads and preserves the marker when payloads are erased;
downgrade refuses any retained marker above one or cursor above seven. The
initial six PostgreSQL 17.11 cases executed the complete historical
Alembic chain, verify row/shape preservation and both cursor bounds, run twelve
real signed actions, round-trip retained v1 history and prove transactional
downgrade refusal. The existing clean SQLite migration regression exposed its
unsupported direct constraint ALTER; Alembic batch operations now preserve that
development/test path while retaining native PostgreSQL ALTER behavior.

The [schema19 release assessment and procedure](BROWSER_TYPED_CONTROLS_SCHEMA19_RELEASE_2026-10-15.md)
identifies every schema-specific consumer. Their allowlists and release targets
remain unchanged, with explicit tests that the schema18 path rejects schema19
before external calls. F7 cutover still targets schema18. Schema19 needs a later
additive release with its own admission, migration and rollout evidence. Do not
enable typed controls against the old constraint or mark deployment complete
from these local checks.

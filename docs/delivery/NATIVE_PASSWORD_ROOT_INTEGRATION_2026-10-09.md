# Native candidate pairing root integration

The reviewed native recent-password boundary is integrated on top of tracked commit
`3b96c795bf6d87f82af394a3ed99b754fe0dbfa2`. All eight frozen source hashes and 26
baseline hashes were checked before application. The connected emulator test is included
in the CI lint selection. Exact verification records are in
[the evidence file](evidence/2026-10-09-native-password-integration.json).

The complete default backend suite passes **1,504 tests with no failures or skips**.
This includes the actual PostgreSQL credential tests, PostgreSQL admission/cost tests,
Redis pacing checks and unique-database Firestore emulator cases. The affected native
boundary separately passes 45 tests. Full CI type checking passes on 83 source files;
affected-domain/script lint, compilation, six prompt fixtures and unchanged OpenAPI
export also pass. The independent frozen-scope audit passed 561 tests with no exclusions.

This connects current SQL password verification, registered candidate identity reads,
fresh protected admission before KMS, native invocation claims and the existing pairing
lifecycle. Definite acknowledgements and current lifetime checks gate each transition.
Ambiguous/existing admissions cannot authorize signing or re-signing. No browser output
contains a credential, nonce or signed assertion. Pairing grants identity only.

The exact prior commit has all four remote CI jobs green (run `37859825074`). That run
does not cover this subsequent source integration. Production API health is 200 and
continues to report release `e7d8f2ba0435fd3d4a504ccdce83fac365ec6835`, with schema
`20261008_0009`; this source has not been deployed. No real KMS call, candidate disclosure,
employer form fill, upload or application submission was made.

Still required before browser assistance can launch:

- Protected account/session lifetime writers and complete replayable denial/restore effects.
- Actual resource identity, IAM separation, retention, KMS credential and restore proof.
- Authenticated HTTP/BFF/MV3 pairing and current action authority for exact reviewed packages.
- Permitted Razorpay/Greenhouse form validation and candidate final-submission controls.
- Review of the real-stack browser harness; independent review found a process-group cleanup
  defect, so that separate frozen patch is held for repair and has not been integrated.
- Preservation of model-cost liabilities after deletion, correct result/deletion lock ordering,
  old-writer retirement, the monetary migration and controlled production rollout.

The earlier source-preserving resume, native LaTeX, document isolation, independent search
coverage, field performance and broader acceptance gates remain open in
[the requirements register](REMAINING_REQUIREMENTS.md). This checkpoint is source evidence,
not completion of the full development or deployment goal.

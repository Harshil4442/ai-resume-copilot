# Initial launch path: candidate-reviewed browser assistance

Decision: on 8 October 2026 the candidate selected **browser assistance first** rather
than supplying an employer/ATS test tenant. Keep the existing GCP backend, Vercel frontend
and GitHub release workflow. This changes launch order, not the permission requirements or
the wider automatic-application acceptance gates.

## Candidate flow

1. Upload a resume and confirm extracted facts and job preferences. Search the verified
   employer-origin index, choose the result count and review the separate search quote.
2. Select an opening and choose the original resume, a custom file or an optional tailored
   version. Tailoring remains separately quoted and optional. Inspect the exact native
   download; unsupported format-preserving edits pause rather than rebuilding the resume.
3. Open the employer's genuine application page in the candidate's own browser. Login,
   MFA, CAPTCHA and assessments stay with the candidate; sessions and passwords stay local.
4. Pair the browser device through authenticated enrollment, inspect the supported current
   form and review every exact answer, file, destination and consent. Explain that filling
   or uploading can trigger employer autosave before the final submission button.
5. Explicitly approve the supported actions. The companion requests a fresh online,
   device-bound decision for each field, rechecks the current page/form and fills only
   approved values. It pauses on navigation, revoked permission, new conditional questions,
   unknown values or uncertain outcomes. No generic unrestricted form-filling agent is used.
6. The candidate reviews the employer page and clicks its final submission button.
   Unsupported file upload or questions remain manual until their adapter is verified.
   Show **filled only**, **paused**, **unknown** or **user-reported submitted** accurately.
   A human click is not a verified automatic submission or an employer receipt.

Deterministic parsers, preference filters, ranking, exact field adapters, approved-answer
reuse, hashing and state transitions perform ordinary work without model calls. Optional
writing/tailoring can use AI only after its separate quote and approval. Unknown personal,
legal, work-authorization or consent answers require the candidate's input.

## Billing and authority

Existing search settlement remains 1 service credit per new qualifying delivered job,
bounded by the candidate's chosen quantity. Optional tailoring uses its existing analysis
units. The 5-credit automatic-application price remains limited to a verified complete
automatic submission. Browser filling, manual upload, portal handoff and user-reported
submission cannot settle that charge. This decision creates no new assisted-filling price
or automatic execution reservation; any later assisted-service fee needs an explicit
versioned quote and reviewed billing contract.

The committed companion is still disabled with zero host grants. Its localhost fixtures
are not production pairing, employer permission or independent recovery evidence. The
Python recovery-core slice likewise supplies no production storage/authentication adapter.
Current automatic-submission flags remain false. Public employer job feeds establish read
capability; browser support needs a permitted, reviewed adapter and narrow origin access.

## Development checklist

- [x] Record the candidate's browser-first launch decision and honest completion/billing labels.
- [x] Preserve the disabled companion and automatic-submission flags while foundations are tested.
- [ ] Verify authenticated candidate/device enrollment, possession, expiry, key rotation,
  account switching, unpairing and minimum origin permissions.
- [ ] Integrate independently retained epochs, fresh approval registration, append-only begun
  evidence and deletion/revocation tombstones; verify the actual production authority.
- [ ] Extend the bounded one-fill core to attempt-bound multi-field steps while keeping the
  stable opening claim across devices, epochs and packages. Never clear it to advance a form.
- [ ] Connect reviewed application packages and exact form versions to the companion; prevent
  restored SQL records or queued messages from registering a fresh candidate approval.
- [ ] Verify the first permitted employer adapter with conditional fields, checkpoints,
  autosave, cancellation, navigation, offline/restart refusal and narrowly scoped file handling.
- [ ] Ship clear install/pair/review/pause/fill-only UI and verify mobile review plus desktop
  companion accessibility. Mobile without a compatible companion retains prepared handoff.
- [ ] Complete staging isolation, security, retention/deletion/restore and browser lifecycle
  acceptance before enabling a limited candidate cohort.
- [ ] Observe the pilot without falsely recording final submission, charging fill-only work
  as automatic completion or claiming broad application coverage.
- [ ] Add authorized automatic final submission only after its distinct fresh launch permit,
  permission, complete-form, cancellation/unknown-outcome and verified receipt gates pass.

See [the companion boundary](BROWSER_COMPANION.md),
[the recovery design](RECOVERY_FOUNDATION_PLAN.md), and
[Stage 4/5 requirements](../requirements/JOB_SEARCH_AND_APPLICATION.md) for the complete
contracts. The current deployed release and its limits are recorded in
[the component release report](BROWSER_FIRST_FOUNDATION_2026-10-08.md).

# Initial launch path: candidate-reviewed browser assistance

Decision: on 8 October 2026 the candidate selected **browser assistance first** rather
than supplying an employer/ATS test tenant. Keep the existing GCP backend, Vercel frontend
and GitHub release workflow. This changes launch order, not the permission requirements or
the wider automatic-application acceptance gates.

The candidate selected **Razorpay / Greenhouse first** on 9 October (Asia/Kolkata).
The [public portal review](RAZORPAY_BROWSER_PILOT_2026-10-09.md) confirms the exact tenant,
extra required DOM fields and earlier-than-submit disclosure paths. This is a local-fixture
reference and rollout priority, not a new browser permission or an enabled integration.
The [pairing boundary review](BROWSER_PAIRING_BOUNDARY_2026-10-09.md) identifies the
stable-account/authentication and independent lifecycle records required before pairing.

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
units. The historical automatic-application price was 5 credits; the reviewed finite-pack
source proposes 20 for new quotes, with production activation still pending. Either price
applies only to a verified complete automatic submission. Browser filling, manual upload, portal handoff and user-reported
submission cannot settle that charge. This decision creates no new assisted-filling price
or automatic execution reservation; any later assisted-service fee needs an explicit
versioned quote and reviewed billing contract.

The current companion package provides reviewed Razorpay text assistance with zero
initial host grants; production assistance remains disabled in the release configuration.
Its installed-browser/SQL fixtures are not deployed authentication, employer permission or
independent recovery evidence. The Python recovery-core slice likewise supplies no
production storage/authentication adapter. Current automatic-submission flags remain false.
Public employer job feeds establish read capability; browser support needs a permitted,
reviewed adapter and narrow origin access. The current limited implementation and wider
recovery requirements are distinguished in [ADR 007](../adr/007-reviewed-browser-assistance.md).

## Canonical-domain acceptance and activation

The shipping companion binds its website origin, transport, optional permissions and
CSP to `https://www.hirewizhq.com`. A Vercel preview cannot prove the installed companion
to deployed BFF path by substituting a preview origin. Preview verification covers assets,
downloaded source bytes and routing/authentication refusals. Genuine preview login requires
an actually registered OAuth callback; canonical cookies or manufactured tokens are not
preview authentication evidence.

After the reviewed cutover, sign in freshly on canonical `www` with assistance disabled.
Verify the real session, saved application, exact resume download and companion page.
The authenticated availability response must report disabled actions and zero fee. Only
then provision the separate `BROWSER_ASSISTANCE_SIGNING_SECRET` and explicitly enable
`BROWSER_ASSISTANCE_ENABLED`; keep `EMPLOYER_AUTO_SUBMIT_ENABLED=false`. Migration
`20261014_0017` and `EMPLOYER_DISCOVERY_ENABLED=true` are prerequisites. The current
assistance flag is global: an acceptance run using one account is not an enforced
one-candidate rollout.

For the first real-session check, install the exact reviewed ZIP in desktop Chrome,
verify extension ID `pnjmegppacleipajnifjmbcopjjffklb` and request only the HireWiz and
Greenhouse optional origins. Compare and confirm the real device fingerprint through
the authenticated website. Inspect one owned application against a fresh matching
Razorpay posting; review its exact destination, file hash, values, manual controls,
email-validation disclosure and zero fee. Approve and cancel before Fill, then revoke
the device. Verify no begun field requests, employer events, submission records or
application credit charges. This establishes deployed authentication/inspection/review/
cancellation, while installed synthetic fill evidence remains separately labelled.

If that check fails, disable assistance without restoring or rewriting financial data.
Cancel/revoke owned authority first when possible, because disabled candidate routes
refuse mutations. Existing field leases can last up to ten seconds; disabling does not
recall prior disclosure. A database restore additionally requires resetting outstanding
authority and rotating the signer before reopening. Live filling or submission needs its
own exact candidate-approved actions and evidence; do not report an application that was
never submitted.

## Development checklist

- [x] Record the candidate's browser-first launch decision and honest completion/billing labels.
- [x] Preserve the disabled companion and automatic-submission flags while foundations are tested.
- [ ] Verify authenticated candidate/device enrollment, possession, expiry, key rotation,
  account switching, unpairing and minimum origin permissions.
- [ ] Integrate independently retained epochs, fresh approval registration, append-only begun
  evidence and deletion/revocation tombstones; verify the actual production authority.
- [x] Add the [disabled local multi-field core](RECOVERY_SEQUENCES_2026-10-09.md) with
  222 focused cases, preserving the stable opening claim across attempts/devices/epochs.
- [ ] Integrate that sequence with actual production authority and verified browser fields;
  never clear the stable claim to advance a form.
- [ ] Connect reviewed application packages and exact form versions to the companion; prevent
  restored SQL records or queued messages from registering a fresh candidate approval.
- [x] Integrate the [Razorpay structural fixture](RAZORPAY_FIXTURE_2026-10-09.md) with 40 focused root cases; this is local DOM scaffolding only.
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
[the current component release report](RECOVERY_AND_CATALOG_RELEASE_2026-10-09.md).

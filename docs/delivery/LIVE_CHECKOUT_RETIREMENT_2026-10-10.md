# Live retirement of new legacy purchases

Verified at 19:45:39 UTC on 9 October (01:15:39 IST on 10 October).

New purchases of the old ₹999 Premium/unlimited and ₹499 service-credit offers are disabled. The API serves the exact existing e7 image with only `RAZORPAY_CHECKOUT_ENABLED=false`; finite packs have not been activated. Existing provider-created orders can still capture and retain their accepted terms. Signature verification, callbacks and refund routes keep the same code and credentials. No live payment was used as a smoke test.

The independently reviewed exact-serving proposal was staged with zero traffic, verified, then promoted to 100% as `ai-resume-parser-e7-checkout-off-20261010`. The longstanding release-candidate URL points to that revision; the unused generation-quiesce maintenance tag was removed after current production Vercel API configuration and its pre-deployment timestamps were checked. Both remaining public tags disable checkout. This is checkout retirement; optional AI generation has not been quiesced.

GET checks through Cloud Run, the custom API domain and both remaining public tags verify the exact healthy revision, a no-store public catalog with every purchase flag false, and HTTP401 for unauthenticated billing configuration. Service IAM remains unchanged. Migration, worker, queue and provider credential changes were not part of this operation. The Cloud Resource Manager API prerequisite was enabled after the initial CLI refusal; the unchanged-service observation, successful dry-run and corrected HTTP header verification remain in private evidence.

- [x] Exact source flag review: accepted capture/refund and signature processing unaffected.
- [x] Checkout-only zero-traffic stage and exact configuration/image verification.
- [x] 100% promotion and checks through every remaining public API route.
- [ ] Separately reviewed frontend retirement of old purchase advertisements.
- [ ] Controlled schema13/backend rollout, funded policy and finite-pack purchase activation.

The [sanitized deployment evidence](evidence/2026-10-10-live-legacy-checkout-retirement.json) records scope and verified identities. Private configuration files contain credentials and must not be published. Public tags are independent of traffic percentage; combined tag and exact traffic changes follow [the Cloud Run traffic reference](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/update-traffic).

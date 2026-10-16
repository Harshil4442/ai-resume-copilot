# Razorpay structural browser fixture

Status: **seven frozen additive files integrated; 40/40 root focused cases passed;
all four exact fixture-commit CI jobs passed**. This hand-authored loopback scaffold uses the
[public portal review](RAZORPAY_BROWSER_PILOT_2026-10-09.md) as a structural reference.
It is not an enabled Razorpay adapter, extension, authenticated authority or production
application service. GCP/Vercel still serve the separately verified `6f38020` release.

The fixture models seven supported text inputs alongside more than eight required
controls, manual Country/Phone, custom comboboxes including candidate-chosen Gender,
repeatable employment and file/text alternatives. Its email-blur and manual-file
recorders demonstrate disclosure before final submission using synthetic local data.
Inspection emits no events. Exact values, before-state, origin/path, tenant/opening,
top frame, form membership, visibility, manual controls and recipient policy are checked.
Changed context, challenges or a selected file pause assistance.

An independent review found that the first handoff implementation treated approved
answers as already filled and could hide six blank required controls after one fill.
The repaired adapter derives completion from unique current DOM controls matching exact
approved values. Every required manual control remains listed. Two regressions fail the
superseded adapter and pass the repair. DOM equality proves neither callback delivery
nor a durable checkpoint or employer receipt.

[Source hashes and root evidence](evidence/2026-10-09-razorpay-fixture-local.json) bind the
repaired patch `ead6579ddbbda52ff7b5f1191c4dc6ef02e9d7ca447bd1f6755754fb7c5ed218`.
The root executed all **5 unit and 35 actual Chromium cases together**, with zero
failures/skips. This is a new 40-case execution, distinct from the agent's retained five
unit results plus repaired 35-case browser rerun. The original failed handoff evidence is
preserved; no shipping verifier or authority repair is replaced by the fixture patch.

CI explicitly runs both new test files using Node24 and the existing lockfile.
Manifest/config/protocol/local-adapter/package/authority/builder bytes remain unchanged
against the root's `6f38020`. The builder copies only shipping extension files, so these
fixtures cannot silently grant employer access. Chromium blocks every foreign request
and service workers; no live employer requests or real candidate data are used.

The short-lived, one-use gate is unauthenticated in-memory test state. It is not wired
to MV3, v2, cloud storage, recovery, billing or pairing. No automated file or submit action,
applied status, employer receipt or application charge is created. The form simulates
conditional widgets and a synthetic country subset; it does not prove current live
selectors, option behavior, arbitrary scripts/egress or portal-use permission.

- [x] Integrate the exact repaired seven-file patch and preserve shipping boundaries.
- [x] Execute the complete 40-case focused suite in the merged root.
- [x] Add explicit unit/browser CI commands without changing shipping build entry points.
- [x] Pass the exact fixture/documentation commit's remote CI.
- [ ] Connect verified pairing/authority, reviewed packages and permitted real DOM actions.
- [ ] Prove actual browser lifecycle, egress/privacy policy and staging acceptance.

See the [fixture README](../../browser-companion/fixtures/razorpay/README.md) for commands
and [browser-first checklist](INITIAL_BROWSER_ASSISTANCE_2026-10-08.md) for release gates.

Exact commit `a72d9a24a79295cf2dfc3690c24056224b3027ca` passed
[CI run 37836366224](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37836366224),
attempt 2: 780 backend cases, 71 frontend unit cases, 190 browser cases, the unchanged
56 protocol/14 MV3 cases and new 5 unit/35 Razorpay browser cases. Attempt 1's frontend
Turbopack Google-font build failed; its logs are preserved. The single failed-job retry
passed without application or dependency changes. Its cause is not established. This
test/documentation-only commit did not require replacing the serving `6f38020` components.

# Browser-first foundation and narrow-phone layout fix

Status: **exact-commit CI passed; frontend promoted and hosted layout verified**.
Frontend `7c59578` is promoted; the GCP API/workers remain `ec52279` as recorded in
[the backend release report](PACING_AND_CATALOG_2026-10-08.md).
This stage records the candidate's browser-first launch order, integrates an inactive
Python recovery core and fixes a small homepage layout problem. It enables no companion,
grant, production authority, upload, automatic submission or additional application charge.

## Changes and local verification

- [Launch order](INITIAL_BROWSER_ASSISTANCE_2026-10-08.md): candidate-reviewed local
  browser filling first, with human final submission and honest fill-only/unknown labels.
  Search and optional tailoring keep their existing separate quotes. Filled/user-reported
  applications cannot settle the automatic-application price.
- [Disabled recovery core](RECOVERY_CORE_2026-10-08.md): strict bindings, one-use approval,
  atomic begin, durable opening claims and monotonic revocation. Its default production
  store is unavailable; the test file is not a production adapter. All 85 focused cases
  and the complete merged 630-test backend suite passed with no skips. Ruff and 53-file
  Mypy, compile, six prompt evaluations and zero OpenAPI drift passed.
- Homepage list counters: two scoped CSS properties prevent flex shrinking and digit
  wrapping. No other frontend source changed. The [local production-build proof](evidence/2026-10-08-home-counter-local.json)
  passed at 320/390/1440 px: all 12 counters remained single-line, adjacent copy stayed
  within its row, and page overflow, console/runtime errors and attempted writes were
  zero. Resume-step numbers stayed intact. TypeScript/build and all 42 page generations
  passed; all 12 screenshots were visually inspected.

Layout evidence preserves the initial temporary anonymous-session fixture error and a
streamed-heading readiness failure. Only the `/tmp` harness was corrected; final checks
reuse the already-passed 320 px source snapshot and verify 390/1440 separately. Aborted
local route-prefetch GETs remain resource warnings. Full case evidence and screenshots
remain supplementary temporary files under
`/tmp/hirewiz-home-counter-local-20261008-1791481853218/`; the committed aggregate is
durable. This targeted layout proof is not field performance or broad browser acceptance.

## Delivery checklist

- [x] Integrate only the frozen recovery files, requirements/decision and two-property CSS fix.
- [x] Pass complete local backend, scoped frontend render/build and static/contract checks.
- [x] Preserve all previous production flags, permissions, prices and hosted evidence.
- [x] Pass exact-commit remote CI before promoting the layout change.
- [x] Stage the exact frontend snapshot with deployment protection retained.
- [x] Verify unchanged API contracts/active backend modules and promote that exact frontend.
- [x] Verify custom-domain identity and narrow-phone counters after promotion.
- [ ] Implement and prove attempt-bound multi-field core, production authority and pairing.

## Exact frontend rollout

[CI 37821078441](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37821078441)
passed all four jobs against `7c59578d169290a91f45d39552ad48d41ea40d16`: **630 backend
tests with no skips, 55 frontend units, 190 browser cases, 55 companion protocol cases
and 14 Chromium MV3 cases**. Backend Ruff, 53-file Mypy, migrations, prompt evaluations,
generated contracts, security checks and builds passed. The companion remains disabled.

The exact tracked frontend snapshot was staged with `--skip-domain`, verified READY
and promoted after CI passed. Authenticated alias metadata at 18:17:41 UTC confirms
`www.hirewizhq.com` resolves to `dpl_3p7SbZsNbSGDbvK47qpTQodwzfMa`, whose commit
metadata matches the tested SHA. Project protection remains `all_except_custom_domains`;
the public custom domain and protected deployment URL retain their existing policy.
See [the deployment identity](evidence/2026-10-08-browser-first-vercel.json) and
[CI evidence](evidence/2026-10-08-browser-first-ci.json).

No GCP, SQL, migration or environment rollout was required. The existing active backend
modules, dependency locks and OpenAPI contracts match the previous serving release.
Public health still returns `ec52279d236e82813bc486b9d5de9eb97a80408d` and
`ai-resume-parser-00257-tov`. The new disabled recovery modules are committed source,
not part of that serving GCP image. [Compatibility evidence](evidence/2026-10-08-browser-first-backend-compatibility.json)
records this component boundary; prior worker and schema evidence remains historical.

[Post-promotion homepage checks](evidence/2026-10-08-browser-first-hosted-responsive.json)
passed **3/3 at 320/390/1440 px**. All twelve observed story counters remained single-line,
with nonshrinking widths and unchanged copy in row bounds. Mobile 44px menu keyboard/tap
controls and mobile/desktop focus checks passed. Nine saved screenshots passed visual
review. Page overflow, runtime/console errors, attempted writes, private reads and external
requests were zero. Three aborted public navigation prefetches are recorded separately;
there were no retries or hidden failures. Single unthrottled DOMContentLoaded samples
were 1641/456/387 ms; these establish neither field p75 nor a causal speed improvement.
Full screenshots and results remain supplementary files in
`/tmp/hirewiz-home-counter-hosted-7c59578-1791483495798/`; the aggregate evidence is committed.

## Deployment credential remediation

A read-only project-settings inspection accidentally printed an automation-bypass
credential in tool output. The exposed entry was revoked through Vercel's dedicated
protection-bypass API; a generated replacement is selected as the managed system variable.
The [sanitized rotation proof](evidence/2026-10-08-vercel-bypass-rotation.json) contains no
credential values. Fresh requests with the revoked secret and with no secret both redirect
to Vercel SSO; the replacement receives 200. The custom domain remains 200 and its exact
alias identity and protection policy are unchanged. Private credential-bearing temporary
files were removed. No rotation mutation was retried: the first verification incorrectly
expected HTTP401, then a read-only redirect check confirmed the normal HTTP302 SSO flow.

This separate credential remediation changes no application source or public behavior.
Existing deployments retain historical injected values, which are now revoked; a consumer
would need rebuilding. No repository reference to that managed variable or bypass header
was found before this incident note. Future builds receive the selected replacement.
Rotation follows [Vercel's automation-bypass contract](https://vercel.com/docs/deployment-protection/methods-to-bypass-deployment-protection/protection-bypass-automation)
and [dedicated API](https://vercel.com/docs/rest-api/projects/update-protection-bypass-for-automation).

The [multi-step plan](BROWSER_MULTISTEP_CORE_PLAN_2026-10-08.md) preserves the permanent
opening claim while allowing an exact owning attempt to advance supported fields. The
[production adapter review](PRODUCTION_RECOVERY_ADAPTER_REVIEW_2026-10-08.md) requires a
buffered Firestore transaction facade, stable journal operation/receipt, conservative
ambiguity handling and independently protected replay barriers. Neither proposal is a
production implementation, and this stage completes no worldwide auto-apply gate.

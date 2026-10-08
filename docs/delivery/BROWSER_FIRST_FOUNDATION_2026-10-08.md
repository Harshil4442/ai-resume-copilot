# Browser-first foundation and narrow-phone layout fix

Status: **integrated and locally verified; exact-commit CI and frontend rollout pending**.
The [current deployed release](PACING_AND_CATALOG_2026-10-08.md) remains `ec52279`.
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
- [ ] Pass exact-commit remote CI before promoting the layout change.
- [ ] Stage the exact frontend snapshot with deployment protection retained.
- [ ] Verify unchanged API contracts/active backend behavior and promote that exact frontend.
- [ ] Verify custom-domain identity and narrow-phone counters after promotion.
- [ ] Implement and prove attempt-bound multi-field core, production authority and pairing.

The [multi-step plan](BROWSER_MULTISTEP_CORE_PLAN_2026-10-08.md) preserves the permanent
opening claim while allowing an exact owning attempt to advance supported fields. The
[production adapter review](PRODUCTION_RECOVERY_ADAPTER_REVIEW_2026-10-08.md) requires a
buffered Firestore transaction facade, stable journal operation/receipt, conservative
ambiguity handling and independently protected replay barriers. Neither proposal is a
production implementation, and this stage completes no worldwide auto-apply gate.

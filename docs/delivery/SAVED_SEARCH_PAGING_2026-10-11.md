# Saved-search paging — development checkpoint

The employer jobs page can browse saved searches and their saved results without
starting another paid search. Two additive owner-authenticated GET routes return
history summaries and result pages. The UI uses ten entries per page; the server
accepts one to twenty. Original result order, requested/delivered counts, charges,
refunds and accepted price quotes remain intact. Preparing an application requires
an explicit candidate action and retains the existing per-job resume review flow.

## Design and compatibility

- Summary SQL excludes the saved result array and uses creation-time/ID keysets.
- Signed opaque cursors bind owner, route kind and boundary. Result cursors also
  bind the saved search and its complete snapshot. A changed snapshot requires
  restarting at its first page; tampered or cross-owner cursors are rejected.
- Both new routes return `Cache-Control: private, no-store`, including static
  validation errors. Invalid transport input is not reflected in responses.
- Historical additive query/scope fields are retained. Unusable metadata produces
  static review guidance; the UI clears stale form/resume state before allowing
  the candidate to select another resume. Financial values are not coerced.
- Legacy endpoints and their contracts are unchanged. The API adds two paths and
  four models. This checkpoint changes no price, reserve, refund, payment,
  application-submission or AI behavior.

## Verification

The original private freeze failed independent review on three defects: malformed
metadata returned a non-private 500, oversized cursor validation reflected input,
and nested role/quote leaves crashed rendering. Its original failures and controls
are retained. The repaired eleven-file freeze passes 109 author backend cases and
402 author frontend executions. Independent review passes the unchanged six
backend controls, 29 necessary JSON/privacy controls and 37 focused frontend
executions, including the unchanged three original UI controls. These suites
overlap; the numbers must not be added as unique coverage.

Root verifies all 441 author artifacts and 22 independent review artifacts before
integration. The composed frontend passes the normal production Turbopack build,
full type checking and edited-file lint. A separate one-file browser fixture
correction handles the new GETs and adds saved-page/free-retry scenarios. Its final
90 Chromium cases pass at 320, 390, 768, 1024 and 1440 pixels without retries,
failures or skips. Every backend API in those browser cases is mocked; this does
not prove the live API/database journey. The ten native full-page screenshots are
retained; five page-two screenshots received inspection at tool-limited resolution,
not a complete visual or accessibility audit. Independent fixture review verifies
all 26 artifacts and 194 composed frontend source hashes, reconciles JSON/JUnit
results, and preserves all 16 original scenarios/assertions and 33 mock response
bodies. Root verifies its eight review artifacts before the one-file integration.

The browser scenarios preserve an original two-credit quote while the current
fixture price is nine, traverse all 27 saved jobs, and verify no mutation/charge
before explicit preparation. An unavailable page retains totals and permits a
free read retry. Fixture prices are test values, not production pricing changes.
The shared preparation response is the existing fixed fixture; the new scenario
proves the selected request payload, not actual returned-package identity binding.

Actual local PostgreSQL 17.11 acceptance verifies keyset ties, Unicode/native JSON
behavior, cross-owner/stale cursors, snapshot changes and unchanged financial rows.
The original 19-case run preserves one timestamp-spelling difference (`Z` versus
`+00:00`, same instant); three focused checks pass. Root verifies 193 artifacts and
owned database removal. This is a component test with an injected owner, not global
authentication, PostgreSQL concurrency or the composed live browser/API journey.

Committed source `4c287dc` passes four CI lanes and frontend 399 units/types/build;
35/245 responsive cases fail from two stale GET mocks. The reviewed two-file repair
is integrated, with a final 245/245 mocked responsive run at five widths and no
retries/skips. Original failures remain retained. Its fresh source CI is pending.

## Remaining requirements

Result paging bounds response size and rendered cards. It still loads, decodes and
hashes the complete saved JSON array. The search's first-1,000 candidate cap,
legacy-history work under the financial lock, durable projection/checkpoint work,
global authentication, PostgreSQL concurrency, real composed backend acceptance,
fresh committed-source CI and production deployment remain open. The local
PostgreSQL component acceptance above closes only that narrower database check.

This does not establish worldwide job coverage or greater-than-95-percent recall.
Checkout, analysis dispatch and production application submission stay disabled
under the existing deployment holds. The protected integration is unchanged.

The original author's earlier broad search had an ineffective recovery exclusion;
the incident remains recorded and prevents a blanket claim of zero protected
author reads. This integration uses only the reviewed ordinary path allowlist.

## Checklist

- [x] Preserve valid old saved results, order and original quotes.
- [x] Add free authenticated summary/result-page routes and UI navigation.
- [x] Repair and independently replay the three original failure boundaries.
- [x] Verify composed production build and responsive mocked-browser behavior.
- [x] Independently clear and integrate the separate browser fixture.
- [x] Verify the actual local PostgreSQL paging component.
- [x] Integrate the two additional fixture corrections; final 245-case responsive run passes.
- [ ] Pass fresh committed-source CI and the composed backend journey.
- [ ] Close bounded candidate/history/database processing requirements.
- [ ] Deploy and observe the final permitted production release.

Machine-readable source and review pins are retained in
[the checkpoint evidence](evidence/2026-10-11-saved-search-paging.json).

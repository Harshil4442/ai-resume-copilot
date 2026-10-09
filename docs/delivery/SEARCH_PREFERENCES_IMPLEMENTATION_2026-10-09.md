# Candidate search preferences — reviewed component checkpoint

This addresses FR01–FR04 explicit preferences. CV05 worldwide recall and independently judged ranking remain open. Author baseline: `4911d1f637c0dab28817e10a41ed352e56d166a3`, captured in a separate archive. R2 independent source review is CLEAR and exact root composition passes its bounded checks. Schema12/13 preflight, full billing/browser integration and deployment remain separate gates.

## Actual contract and behavior

`SearchCreate.preferences` is optional. Omitted/null preferences preserve the exact historical input fingerprint and saved-search retry behavior. New clients send version 1 with unanswered choices null: country codes, posting display-language codes, employment types, salary bounds/currency/period, need for sponsorship, stated work-authorization countries and willingness to relocate. An empty authorization-country list records an explicit no-authorization answer; null remains unanswered. Boolean answers are strict. Duplicate/empty filters, invalid salary ranges, nonfinite amounts, unknown fields and unsupported versions are rejected. Bounded country/currency syntax is not a full international taxonomy or legal-status verification.

Known country/locale alternatives use any selected choice. Employment is evaluated conservatively across separate hours, contract-tenure and programme axes: permanent alone cannot contradict full-time. One potentially compatible or unknown alternative keeps the job visible. Salary ranges overlap inclusively only when currency and pay period match exactly. There is no FX conversion, annualization, AI call or salary extraction from prose.

Missing, malformed, unsupported-version or wrong-provider metadata stays unknown. Unknown facts remain included; `unknown_metadata` is fixed to `include`. No reviewed public connector currently supplies sponsorship/work-authorization/relocation facts, so those choices remain needs-review on live feeds. Relocation answers are retained without inventing whether moving is required. Results never certify eligibility or hiring success.

Exact preferences are persisted in search JSON, input fingerprint, result snapshots and reservation price-quote fingerprint. Quote provenance retains actual unit price, pricing version, requested count and maximum credits. Changed preferences with the same owner/key yield 409. Previously delivered openings are not charged again; reservations settle for new unique jobs only. Application charges, analysis units and Premium remain separate.

The query still enforces open/enabled/fresh sources, role, literal location, remote/date constraints and exclusions before the existing 1,000-candidate bound. Preference evaluation follows within that set before selection. Scope records candidate count before preferences, known conflicts excluded and candidates with unknown preferences. These counters do not remove unknown/excluded jobs from the separate independent coverage denominator. Candidate-cap shortfalls are stated in UI; stable score/title/id ordering, count limits and owner/history bounds remain.

## Reviewed structural provider facts

| Provider | Facts retained | Official source |
|---|---|---|
| Lever | `country`, `categories.commitment`, optional `salaryRange` bounds/currency/interval | [Postings API](https://github.com/lever/postings-api) |
| Ashby | Primary/secondary structured countries, `employmentType`, exactly one supplied salary summary component | [Public posting API](https://developers.ashbyhq.com/docs/public-job-posting-api) |
| SmartRecruiters | `location.country`, mapped `typeOfEmployment.label` | [Posting API endpoints](https://developers.smartrecruiters.com/docs/endpoints) |
| Personio | XML `employmentType`/`schedule`, configured public-feed display locale | [XML integration](https://developer.personio.de/docs/integration-of-open-positions) |
| Pinpoint | Configured public-feed locale; translations can fall back to English | [Public postings feed](https://developers.pinpointhq.com/docs/jobs-json-endpoint) |
| Greenhouse/Workable | No additional reviewed preference facts in this version; discovery remains supported | No custom-label/prose inference |

Known facts retain precise field path, documentation URL and SHA256 of public structural input. No candidate credential or authentication input is hashed here. Normalized posting content hashes include metadata; saved results copy that exact state. Existing source region, remote labels and the legacy `language='en'` default are not country/authorization/working-language evidence. Sources reviewed 2026-10-09.

Metadata JSON: version 1, normalization `employer-preferences-v1`, provider and seven typed facts: country_codes, posting_languages, employment_types, salary, sponsorship_available, work_authorization_required, relocation_supported. Each fact has state, nullable value, bounded field evidence and unknown reason. Nullable old rows stay unknown without synthetic backfill.

Ashby compensation is opt-in with `includeCompensation=true`. This patch preserves separately owned request construction: normalize compensation if supplied; the optional retrieval/conformance seam remains open. Multiple salary components or unsupported intervals stay unknown. Alpha-3 countries use a small 13-code mapping; unsupported values stay unknown. Complete worldwide metadata support is not claimed.

## Role vocabulary and UI

`role-aliases-v1` expands only software engineer/developer/SDE, backend engineer/developer/back-end, and frontend engineer/developer/front-end. The literal branch and all other qualifiers (Senior, Python etc.) remain. PostgreSQL full-text and literal clauses use the same bounded OR branches. This is a small deterministic vocabulary; independent ranking quality is unproven. Basic skill fit remains resume-evidence overlap, not hiring probability.

Additional accessible controls start collapsed, with null defaults and unknown-data help. Invalid choices disable the paid request. Interrupted requests retain the exact key/preferences. Saved history restores role/location/count/date/remote/exclusions/preferences. Result cards show missing facts and known-conflict/bounded-scope summaries. Browser UI fixture checks and actual backend/PG transaction evidence are explicitly separate.

## Migration and release dependency

Sole additive revision `20261009_0012_employer_search_preferences.py` follows0011 and adds nullable JSON `employer_postings.preference_metadata`. Existing postings, customer records, ledgers and native authority are unchanged. Actual PostgreSQL upgrade/downgrade proves old rows stay unknown and schema 11 cannot serve the schema 12 ORM. Root must review schema 12 monetary/release/preflight compatibility before promotion; no head 12 snapshot is mislabeled as 11.

Migrate and verify first, drain old ingestion workers, then deploy compatible normalization/API workers and UI. Old writers cannot refresh/clear this column and must not keep publishing while new preference facts are used. Normal new refresh replaces absent facts with explicit unknown. Rollback must stop schema 12 code before dropping metadata; postings survive, collected optional facts do not. This is a separate release review.

## Checklist

- [x] Versioned choices, strict bounds and legacy fingerprint compatibility.
- [x] Nullable migration, reviewed structural normalization and unchanged public-read transport.
- [x] Deterministic known-conflict filtering, visible unknowns and explanatory scope.
- [x] Exact preference/quote/idempotency/snapshot provenance and separate credits.
- [x] Conservative aliases preserve all non-role qualifiers.
- [x] UI choices, retry and saved-search restoration authored.
- [x] Author backend/PG/unit proof; terminal counts and browser evidence recorded in frozen report.
- [x] R2 independent source review; 32 original and 12 new actual PostgreSQL probes pass.
- [x] Root exact patch composition; 313 backend and 218 frontend cases pass, with no API contract drift.
- [ ] Schema12 monetary/release preflight and deployment ordering proof.
- [ ] Ashby optional compensation retrieval and broader reviewed structural mappings.
- [ ] Independent query corpus/relevance judgments and CV05 recall measurement.
- [ ] Production rollout/live source conformance; no >95% coverage claim.

## R2 metadata repair checkpoint

The exact unchanged independent V1 suite reproduced **23 pass / 9 fail** before repair. Its original report and red probes remain immutable. R2 independent source review is CLEAR. Root composition passes 313 backend and 218 frontend cases; full release and deployment remain HOLD. Original broad browser evidence remains 75 pass / 5 old SKU assertion failures, pending the three-pack billing integration.

Stored metadata version 1 now requires an actual integer. Boolean/float/string lookalikes are malformed and cannot create a confirmed conflict. Known employer country, locale, employment and authorization lists must be nonempty and bounded; a malformed stored fact leaves metadata unknown. An explicit candidate authorization answer `[]` remains distinct from unanswered `null`.

Ashby absent/null or a valid empty secondary-location list means there are no supplied secondary addresses. Falsy non-lists, invalid members, excessive lists or any unresolved structured country leave country unknown; separately valid employment/pay facts remain usable. [Ashby documents](https://developers.ashbyhq.com/docs/public-job-posting-api) primary and secondary structured address fields.

[Lever documents](https://github.com/lever/postings-api) a primary location that also appears in allLocations. Our conservative policy accepts its direct country field for legacy payloads without allLocations. When allLocations is present, a known country requires exactly one nonempty location matching the primary location; that scope is retained in field provenance. Multiple, empty, malformed or mismatched location sets stay unknown. The country of an extra city is never inferred from its name. This is a declared compatibility/completeness policy, not proof of exhaustive geographical eligibility or live-feed conformance.

Before enabling hard country exclusions on a previously ingested Lever catalog, refresh/re-normalize those postings with this policy (or quarantine their country facts as unknown). Historic fact digests cannot recover allLocations discarded by the V1 normalizer. Drain prior ingestion writers first; the new worker refresh replaces stale country facts with explicit unknown. No synthetic backfill, customer mutation, new migration or automatic production promotion is part of this repair.

The employer jobs page's existing credit-pack links now use `/billing`; expense policy/catalog ownership remains separate. The repair preserves candidate snapshots/idempotency, credits, unknown inclusion and coverage denominators. Worldwide recall, ranking judgments, schema 12 release/preflight, root composition and deployment remain separate gates.

## Root composition evidence

All 682 source and 728 author artifact entries, and all 682 source and 755 independent artifact entries, were verified before applying the reviewed 21-path patch to `b50d9f7`. The Workable public-read class is unchanged; only reviewed normalization seams and the nullable posting metadata column were added. Root sorted imports in the new R2 test after reproducing one lint finding; no assertions changed. Root lint/type checks pass, Mypy checks 89 domain files, and regenerated OpenAPI/types have no drift.

The [root evidence](evidence/2026-10-09-search-preferences-root-integration.json) records exact resulting path digests and boundaries. The broad browser failures remain in the frozen independent evidence. These checks establish the integrated preferences component, not complete metadata coverage, live eligibility, schema promotion or production rollout.

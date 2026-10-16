# Single-statement authenticated catalog snapshot

Status: **immutable `e7d8f2b` CI passed; GCP/Vercel promotion and bounded live checks verified**.
Exact identities and observations are in the [component release report](CATALOG_SNAPSHOT_RELEASE_2026-10-09.md).
This removes one database statement from GET `/api/v1/employer-jobs/catalog`.
The earlier [diagnostic release](CATALOG_TIMING_2026-10-09.md) measured two
statements; its five production observations are historical evidence, not proof
of this change's speed or a cause such as network, pool wait or cold start.

The route independently revalidates the JWT using the same shared decoder as
generic authentication. One statement then reads the exact owner's ID/balance
and a left join to the enabled-source subquery, ordered by employer/ID and capped
at 500. A missing owner returns the existing 401 with the Bearer challenge;
an existing owner with no sources returns its balance and an empty list. The
projection excludes owner passwords/contact data. Scalar balance and refreshed
source objects avoid stale session identity-map values. No balance or catalog
response is cached. Generic authentication and financial locked reads remain
unchanged; this snapshot cannot reserve credits or authorize an application.

The response retains pricing, configuration, source metadata, private/no-store
headers and the generated API contract. There are no migration, dependency,
region, resume, submission or payment changes. Equal employer names now have
stable ID order within the same 500-source cap.

## Explicit measurement contract

The completion event is `catalog_latency_v2`. `catalog_snapshot_ms` measures the
combined owner/source SQL execution and ORM row loading. `catalog_materialize_ms`
measures only in-memory source extraction. The optimized route leaves the old
`auth_lookup_ms` and `catalog_sources_ms` null and omits their timing headers.
Those legacy phases remain recognized for old direct service/helper calls.
The BFF accepts the two new numeric phases through its existing strict allowlist;
its streaming and error behavior are unchanged. Acquisition remains a composite
observation; connect, ping, RTT and pool wait are unavailable.

Compare total/request durations after promotion, with exact release/revision
correlation and the phase version recorded. Do not compare the new combined
query duration to the old authentication-only field as though their scopes match.
Bounded sequential samples cannot establish field percentiles or load capacity.

## Verification and remaining evidence

- 39 focused catalog/diagnostic cases pass, including real SQLite and PostgreSQL
  queries, owner isolation, zero sources, deleted/missing owner, cached objects,
  changed configuration, JWT expiry between middleware/dependency and 501 tied
  sources. Invalid tokens execute no SQL; accepted catalog reads execute one.
- The merged backend passes 930 cases without failures/skips, including real
  PostgreSQL credit/admission and Redis pacing tests. Ruff and Mypy pass; Mypy
  checks 61 source files. Six prompt evaluations and compilation pass. Three
  existing dependency deprecation warnings remain.
- The frontend passes lint, types and 72 unit cases, including distinct phase
  forwarding. Backend OpenAPI and generated frontend contracts have no drift.
- Independent read-only review found the original timing-label mismatch; the
  versioned explicit phases repair it. Final source review found no confirmed
  defect in the bounded authentication/query/diagnostic scope.
- [x] Pass all four exact immutable-commit CI jobs.
- [x] Take a fresh protected backup, promote the pinned GCP image and Vercel build,
  and verify worker access/configuration and the serving domain.
- [x] Measure at most five direct authenticated catalog reads, correlate events,
  verify the new phase contract and record observations without percentile claims.
- [x] Validate hosted responsive UI and bounded release errors.

The first catalog read took 4,272.493ms, including 3,266.5ms of composite
acquisition; the four subsequent reads took 958.989–966.921ms. All five are
retained. This does not establish the first read's cause, a field percentile,
sustained speed, or a causal improvement over the differently scoped prior run.

The [pairing-only source](PAIRING_ONLY_FOUNDATION_2026-10-09.md) travels in this
release but has no routes or available production store/authentication/claim
issuer. Its inclusion cannot enable browser filling, upload, submission or charges.

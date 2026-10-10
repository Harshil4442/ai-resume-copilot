# Proposed source review — 8 October 2026

All 16 proposed employer→tenant mappings and unchanged public adapters were independently verified (969 jobs; 101 India-location regex matches). These are snapshot counts, not eligibility or coverage. All proposals remain nonlive; no source/doc edits, production writes, submissions or candidate disclosures.

| Priority employer | Exact verified tenant/proof | Jobs /India-location matches | Recommendation |
|---|---|---:|---|
| [Meesho](https://www.meesho.io/jobs) | Lever `meesho`; official self-hosted jobs script uses exact public API |60 /57 | Operator-review candidate; Lever host pacing required. |
| [Mindtickle](https://www.mindtickle.com/careers/) | Official redirect to `jobs.lever.co/mindtickle` |20 /19 | Operator-review candidate; Lever host pacing required. |
| [Harness](https://www.harness.io/company/jobs) | Inline Greenhouse `harnessinc` API URL |65 /8 | Defer explicit website/content-use restriction. |
| [Observe.AI](https://www.observe.ai/careers) | Official Greenhouse embed `for=observeai` |12 /5 | Operator-review candidate; no applicable discovery prohibition located. |
| [Notion](https://www.notion.com/careers) | Exact Ashby `notion` job hyperlinks |135 /4 | Defer unresolved careers-content rights scope. |
| [Mixpanel](https://mixpanel.com/jobs/) | `/careers/` redirects to `/jobs/`, linking exact Greenhouse `mixpanel` |51 /4 | Operator-review candidate; no applicable public-feed prohibition located. |

No unrestricted commercial redistribution license was established. Generic product-service terms were not treated as automatically governing career feeds.

## Employer website terms/robots

- **Meesho:** [careers robots](https://www.meesho.io/robots.txt) has no disallow; no careers legal link located. Ecommerce terms were not substituted.
- **Mindtickle:** [robots](https://www.mindtickle.com/robots.txt) permits careers; [terms](https://www.mindtickle.com/legal/terms-of-service/) concern ordered subscription/professional services, not a located public Lever-feed ban.
- **Harness:** [website terms §§2–4](https://www.harness.io/legal/website-terms-of-use) restrict automated indexing without written consent, systematic content storage, commercial exploitation and linking. [Robots](https://www.harness.io/robots.txt) permitting careers does not resolve that hold.
- **Observe.AI:** [robots](https://www.observe.ai/robots.txt) blocks unrelated marketing paths; [terms](https://www.observe.ai/terms) concern contracted SaaS/Order Forms. No applicable careers-indexing prohibition located.
- **Notion:** [robots](https://www.notion.com/robots.txt) permits careers; the [careers-footer Terms & Privacy destination](https://www.notion.so/28ffdd083dc3473e9c2da6ec011b58ac) did not establish sufficiently clear current careers-content reuse rights. This is uncertainty, not a claim that SaaS restrictions automatically ban the feed.
- **Mixpanel:** [robots](https://mixpanel.com/robots.txt) does not block careers; [terms](https://mixpanel.com/legal/terms-of-use/) concern subscribed Application Services, not a located public Greenhouse-feed ban.

## Actual API-host pacing

**Lever:** [API robots](https://api.lever.co/robots.txt) declares `Crawl-delay:1`; [hosted-jobs robots](https://jobs.lever.co/robots.txt) separately declares 1. Runtime ingestion uses the API host. [Official docs](https://github.com/lever/postings-api) explicitly anticipate third parties scraping published posts; internal posts remain hidden. Recommend at least 1 second aggregate spacing on `api.lever.co` across tenants/workers, pagination and retries. Current pagination is unpaced. These two current boards need one page each, which does not guarantee future compliance. No EU-host rule is inferred from global robots.

**Greenhouse:** [API robots](https://boards-api.greenhouse.io/robots.txt) disallows `/embed/`, not `/v1/boards/.../jobs`; no crawl-delay requirement located. [Docs](https://docs.greenhouse.io/job-board.html) permit unauthenticated GET access. Employer-website rules and public API access remain separate; GET grants no submission permission.

**Ashby:** [API robots](https://api.ashbyhq.com/robots.txt) returned 401 (unavailable, not allow-all). [Hosted-jobs robots](https://jobs.ashbyhq.com/robots.txt) blocks `/meeting/`, `/b/`, `/api/` on that separate host. [Public-feed docs](https://developers.ashbyhq.com/docs/public-job-posting-api) describe employer careers pages; adapter correctly excludes rows unless `isListed` is exactly true. No API-host spacing rule located. [Dedicated partner feeds](https://developers.ashbyhq.com/docs/dedicated-partner-job-feeds) offer customer opt-in; this is an available permissioned route, not proof that every other public read is prohibited.

Implement pacing specifically for the observed Lever API-host declaration. Do not present optional courtesy limits elsewhere as verified vendor requirements. Preserve bounded/complete scans and existing outage/partial-scan protection.

## Remaining scope and enrollment boundary

The other 10 tenants are technically compatible, with exact `Linear` capitalization and `sourcegraph91` preserved. Terms triage is preliminary. [Docker terms](https://www.docker.com/legal/docker-terms-use/) restrict automated access except permitted cases such as documented APIs; applicability to its external Ashby feed needs review. [Ramp robots](https://ramp.com/robots.txt) and [Sourcegraph robots](https://sourcegraph.com/robots.txt) returned 403. Do not bulk-seed 16.

Keep explicit adapter deferrals: Mercari India legacy Workable endpoint returned 302; Dream Sports exact Lever feed returned 404; Groww EU Greenhouse unsupported; FreeAgent Pinpoint tenant unproved; BrowserStack Workday unsupported.

Any accepted subset needs independent operator source-use review and exact platform/region/tenant/hosts. Keep `submission_enabled:false` and no write grants, credentials, parity or receipt claims. Seeder dry run is structural validation; reviewer-email is an audit identity, not authentication. Use the authenticated admin boundary/authorized operator environment after release. Default existing-tenant updates preserve prior write fields; they do not revoke an existing grant.

Detailed sanitized evidence: [source-review observations](evidence/2026-10-08-source-proposal-review.json) (UTC observations, page/script hashes, exact origins, adapter counts). No source was enrolled.

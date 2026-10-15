# Employer origin registry expansion — 13 October 2026

This is implementation evidence within outcome 1 (official search/ranking/counts). It expands the reviewed seed from four to twenty employer origins. It does not complete that outcome, establish worldwide/95% coverage, enable a connector, seed production, or authorize application submission. The original four rows retain their 8 October observations.

## Newly reviewed origins

Each company URL below was fetched publicly on 13 October 2026. Its page links or embeds the literal ATS tenant shown; Commercetools uses an explicit inline BOARD constant with the matching Greenhouse fetch expression. The corresponding public feed returned HTTP 200. Existing tenant/host destination guards admitted every listed requisition URL examined. Feed count is a point-in-time observation, not an independent completeness audit or unique global vacancy count.

| Employer | Official company careers page | Public ATS feed | Listed requisitions observed | India-location matches | UTC feed check |
| --- | --- | --- | ---: | ---: | --- |
| Harness | [www.harness.io](https://www.harness.io/company/jobs) | [greenhouse/harnessinc](https://boards-api.greenhouse.io/v1/boards/harnessinc/jobs?content=true) | 67 | 9 | 2026-10-13T16:49:17.766125+00:00 |
| Observe.AI | [www.observe.ai](https://www.observe.ai/careers) | [greenhouse/observeai](https://boards-api.greenhouse.io/v1/boards/observeai/jobs?content=true) | 10 | 5 | 2026-10-13T16:49:20.286221+00:00 |
| Notion | [www.notion.com](https://www.notion.com/careers) | [ashby/notion](https://api.ashbyhq.com/posting-api/job-board/notion) | 131 | 3 | 2026-10-13T16:49:21.137682+00:00 |
| Mixpanel | [mixpanel.com](https://mixpanel.com/jobs/) | [greenhouse/mixpanel](https://boards-api.greenhouse.io/v1/boards/mixpanel/jobs?content=true) | 55 | 4 | 2026-10-13T16:49:22.242169+00:00 |
| Amplitude | [amplitude.com](https://amplitude.com/careers) | [ashby/amplitude](https://api.ashbyhq.com/posting-api/job-board/amplitude) | 39 | 2 | 2026-10-13T16:49:23.185501+00:00 |
| Vercel | [vercel.com](https://vercel.com/careers) | [greenhouse/vercel](https://boards-api.greenhouse.io/v1/boards/vercel/jobs?content=true) | 90 | 2 | 2026-10-13T16:49:24.021982+00:00 |
| Commercetools | [commercetools.com](https://commercetools.com/careers) | [greenhouse/commercetools](https://boards-api.greenhouse.io/v1/boards/commercetools/jobs?content=true) | 32 | 0 | 2026-10-13T16:49:24.875975+00:00 |
| Sentry | [sentry.io](https://sentry.io/careers/) | [ashby/sentry](https://api.ashbyhq.com/posting-api/job-board/sentry) | 46 | 0 | 2026-10-13T16:49:25.719055+00:00 |
| Docker | [www.docker.com](https://www.docker.com/career-openings/) | [ashby/docker](https://api.ashbyhq.com/posting-api/job-board/docker) | 64 | 0 | 2026-10-13T16:49:26.671423+00:00 |
| Linear | [linear.app](https://linear.app/careers) | [ashby/Linear](https://api.ashbyhq.com/posting-api/job-board/Linear) | 31 | 0 | 2026-10-13T16:49:27.646409+00:00 |
| Reddit | [redditinc.com](https://redditinc.com/careers) | [greenhouse/reddit](https://boards-api.greenhouse.io/v1/boards/reddit/jobs?content=true) | 145 | 0 | 2026-10-13T16:49:28.457285+00:00 |
| Ramp | [ramp.com](https://ramp.com/careers) | [ashby/ramp](https://api.ashbyhq.com/posting-api/job-board/ramp) | 164 | 0 | 2026-10-13T16:49:29.380937+00:00 |
| Sourcegraph | [sourcegraph.com](https://sourcegraph.com/jobs) | [greenhouse/sourcegraph91](https://boards-api.greenhouse.io/v1/boards/sourcegraph91/jobs?content=true) | 10 | 0 | 2026-10-13T16:49:30.384465+00:00 |
| Freetrade | [freetrade.io](https://freetrade.io/careers) | [ashby/freetrade](https://api.ashbyhq.com/posting-api/job-board/freetrade) | 5 | 0 | 2026-10-13T16:49:31.268833+00:00 |
| Pinecone | [www.pinecone.io](https://www.pinecone.io/careers/) | [ashby/pinecone](https://api.ashbyhq.com/posting-api/job-board/pinecone) | 5 | 0 | 2026-10-13T16:49:32.093048+00:00 |
| LangChain | [www.langchain.com](https://www.langchain.com/careers) | [ashby/langchain](https://api.ashbyhq.com/posting-api/job-board/langchain) | 100 | 0 | 2026-10-13T16:49:32.893184+00:00 |

The sixteen feeds returned 994 listed requisition records. 6 feeds contained 25 location-string matches for India/major Indian cities. Location matching is descriptive and does not establish work authorization, remote-country eligibility or an India-wide denominator. Sourcegraph’s prospect-pool record was excluded from the requisition count.

## Exclusions and boundaries

- BrowserStack links Workday, which is outside the current supported source schema.
- Confluent returned HTTP 429 and Rubrik HTTP 403; neither was retried or enrolled.
- Grafana’s requested careers path returned 404. Dropbox’s initial page redirected outside the verified company-host scope; it was not followed or enrolled.
- Cloudflare, Datadog, Anthropic, DigitalOcean, Elastic, Postman, Chargebee, Coinbase, MongoDB, GitLab, Discord, Airtable, Asana, Twilio, Okta, Databricks, PhonePe, Meesho, Miro, HubSpot, ServiceNow, Visa, Zapier, Automattic, Algolia and Mindtickle lacked an exact supported tenant join within the bounded pages examined. This is an unverified source result, not a claim that the employer has no postings or public feed.

Public collection used no authenticated provider/account access: 81 direct script GETs, maximum two concurrent official-page reads, one feed request at a time, a half-second pause between feed requests, no retry after failure, eight-second request timeouts, eight-MiB response admission and a 10,000-record feed bound. This count excludes read-only web-tool views used to locate official links. Company redirect handling allowed only HTTPS and the same company domain; ATS links were recorded but not followed as page redirects. Responses with access or rate-limit errors did not qualify.

## Admission and rollout

The unchanged SourceCreate classes and load_reviewed_sources function were AST-extracted and executed with their real Pydantic dependency in isolation; no app aggregate, configured database or protected module was imported. All twenty rows passed. Negative checks rejected duplicate tenants, HTTP evidence, unallowed company hosts and uncontracted submissions. All submission flags remain false and no credential/grant/form-parity/receipt contract is supplied.

Review and merge the registry, then separately run the existing audited dry-run/apply seeder against an approved target and refresh the feeds through the real workers. Observe source health, quotas, closure behavior, ranking and result-count/refund acceptance before considering outcome 1 complete. Preserve existing per-host pacing and full-scan safeguards. This registry change alone does not enable full-catalogue search or alter prices/credits.

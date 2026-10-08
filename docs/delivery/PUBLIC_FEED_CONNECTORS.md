# Workable, Personio and Pinpoint public discovery

Implemented from current official vendor documentation, checked 8 October 2026.
These adapters expand Stage 2 reads; they grant no submission, login, upload or browser
autofill capability. Tenant verification and independently measured coverage remain
separate release gates.

## Documented contracts

| Provider | Read contract | Identity, locale and dates |
| --- | --- | --- |
| Workable | `www.workable.com/api/accounts/{tenant}?details=true`, complete public `jobs` collection | Keep shortcode, requisition code, geographical city/state/country and explicit remote fields. `published_on` is publication; creation is not substituted. Missing language is `und` (unknown). |
| Personio | Verified `{tenant}.jobs.personio.de` or `.com` `/xml`, employer-enabled feed | Keep numeric position ID, office and requested supported language (default German). `createdAt` is not proof of publication, so publication remains unknown. |
| Pinpoint | `{tenant}.pinpointhq.com/{locale}/postings.json`, full public `data` collection | Keep each posting ID, its hosted UUID/path and parent requisition separately. This preserves multiple postings per job. Default locale is English; translations may fall back to English. Publication remains unknown; explicit past deadlines exclude jobs. |

Primary references: [Workable public API](https://workable.readme.io/reference/jobs-1),
[Personio XML fields](https://developer.personio.de/docs/retrieving-open-job-positions),
[Personio feed activation and .com host](https://support.personio.de/hc/en-us/articles/207576365-Integrate-jobs-from-Personio-into-your-website-via-XML),
[Personio hosted job links](https://developer.personio.de/docs/integration-of-open-positions),
[Pinpoint postings/locale contract](https://developers.pinpointhq.com/docs/jobs-json-endpoint).

## Failure and safety boundaries

Only documented constructed endpoints are read. Personio suffix and locale come from
the verified careers URL; there is no suffix probing, arbitrary fetch URL or redirect
following. Pinpoint avoids deprecated `jobs.json`, which omits posting variants.
Workable's globally shortened `/j/{shortcode}` URL is accepted only when it matches
the shortcode returned by the verified tenant's public feed. Hosted tenant paths,
posting paths, HTTPS and explicit employer host permissions are checked.

All three share bounded unauthenticated GET reads: credentials and cookies are stripped,
responses are capped at 8 MiB, complete collections at 10,000 postings, and scans at
240 seconds. Transient reads have at most three attempts with bounded cooldown;
submission calls are never made. Both reads of the complete collection must agree.
Unexpected pagination metadata, a missing collection, malformed rows, duplicates,
response truncation or a later outage fail the full scan. Existing content and open
state remain intact. Empty collections establish closure only after both reads succeed.

The Personio parser rejects all DTDs before entity expansion, including UTF-16 documents;
it never processes XInclude or resolves external resources. It also bounds XML depth
and nodes, verifies the root/position structure and rejects duplicate scalar fields.
Description HTML becomes inert text. A feed missing usable description text currently
fails with `incomplete_posting_payload`; it does not fabricate qualifications. A future
minimal-record path must expose missing-description quality before ranking/tailoring.

## Evidence and limits

`backend/tests/test_employer_feed_adapters.py` verifies documented shapes, locales, distinct
posting identities, unknown dates, remote/location fields, URL/tenant checks, completed
empty feeds, truncation/pagination rejection, bounded collection/HTTP/XML parsing, DTD
and XInclude rejection, public-only reads and registration without submission rights.
Database outage fixtures for every provider prove that a failed verification read
does not close or overwrite existing jobs.

Fixtures do not establish a live employer-origin audit, a write grant, worldwide recall
or a fleet-wide rate-limit benchmark. Sources whose feeds are disabled, descriptions
are unavailable, responses exceed limits or contracts differ stay unavailable until
an appropriate reviewed adapter or source configuration is supplied.

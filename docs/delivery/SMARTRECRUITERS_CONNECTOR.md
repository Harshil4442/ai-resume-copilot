# SmartRecruiters public employer discovery

Implemented and checked against current official documentation on 8 October 2026.
This adapter completes the initial four-family discovery implementation described in
Stage 2. It does not establish worldwide job recall or employer submission permission.

## Contract and implementation

The adapter uses the company-specific public Posting API, with `destination=PUBLIC`,
pages of at most 100, and explicit offsets. It constructs the list and detail URLs
from a verified company identifier and posting ID/UUID; it never follows a response's
arbitrary `ref`. The separate authenticated job-board publication feed is not used.
[List reference](https://developers.smartrecruiters.com/reference/v1listpostings),
[detail reference](https://developers.smartrecruiters.com/reference/v1getposting).

Public reads strip authorization, SmartToken and cookie headers even from an injected
client. Posting company identity must match the registry. Hosted job/apply URLs must
belong to that tenant and posting, or an explicitly verified employer hostname.
Redirects are not followed. Discovery cannot enable the existing Greenhouse-only
submission path.

Every list page must have valid offset, limit, total and expected length. Changing
totals, duplicate IDs, malformed detail records or missing boolean status fail the
whole scan. After all details, the adapter reads the list manifest again to detect
equal-count replacements and release changes. Only a completed stable scan returns
data to the refresh transaction; failed scans preserve existing open jobs and content.
Offset APIs cannot provide a transactional snapshot: this consistency check reduces
churn risk, and a changing source is retried later rather than partially published.

Detail `active=false` excludes an unpublished job. Text comes from the documented
job-ad sections and is converted to inert plain text. Location, explicit remote
status, country code, language and requisition are preserved. The date stored as
`publication_at` is **the provider's `releasedDate`**, which can represent a release
or republication; it is not proof of original first publication. Missing/invalid
dates remain unknown, and `source_updated_at` is not invented. Release-date changes
alone do not change the content fingerprint.
[Posting objects](https://developers.smartrecruiters.com/docs/objects).

Reads are sequential with a minimum 150 ms interval, an 8 MiB response limit,
10,000-posting ceiling and 240-second scan budget. Each transient GET has at most
three attempts; a provider cooldown longer than two seconds is deferred to the
durable scheduler. Permanent errors, redirects and malformed JSON are not retried.
Large sources that cannot complete within the budget remain unavailable until a
checkpointed adapter is implemented; partial results do not establish closure.
These local bounds operate below the documented per-customer request/concurrency
limits, but do not claim a fleet-wide vendor quota guarantee.
[Customer API policies](https://developers.smartrecruiters.com/docs/customer-overview).

Manual application preparation rechecks one posting directly. The response explicitly
states that the full hosted form is required. It cannot authorize uploads, autofill
or automatic submission, and contains no invented question schema or receipt.

## Evidence and remaining gates

`backend/tests/test_employer_connectors.py` covers complete pages/details, UUID lookup,
dates, locations, language, HTML safety, bounded GET retry/cooldown, credential stripping,
identity/URL rejection, source churn, byte/time ceilings and read/write separation.
Its database outage test proves no partial content or job closures persist after a
detail failure, and that a later completed empty scan can close jobs.

The connector and existing employer service suites passed together (67 tests).
Ruff and Mypy passed for the changed backend modules. Fixtures use synthetic candidate
and employer data; they do not imply a production tenant audit, live contract grant,
load benchmark or an independently measured coverage result. G02A–D still require
their wider registry, coverage and end-to-end evidence from the acceptance register.

# Retained non-Google model capability retirement

Status: read-only source implementation; no provider key was deleted by this
component. Root observed the Groq error protocol using an intentionally invalid
synthetic 56-byte token: verified TLS, fixed official endpoint, HTTP 401 and JSON
`error.code == "invalid_api_key"`, with no inference or user key. This establishes
the protocol contract, not retirement of any retained credential.

The unchanged complete retained-configuration collector found historical Google,
Groq and OpenAI credentials. A model alias such as `LLM_API_KEY` does not identify
its provider. Google keys keep native key resource/UID/issuer/account/getKeyString
checks. Known Groq/OpenAI credentials are classified for their own fixed provider
origins and retained with their exact original bytes and origins in immutable,
representation-hidden process memory. Nothing removes their history or gives them
Google ownership. Unsupported admin issuance tokens refuse outright.

## Accepted capability evidence

The provider verifier sends only a TLS-verified `GET` to the fixed
`https://api.openai.com/v1/models` or
`https://api.groq.com/openai/v1/models`, using the exact retained value as Bearer
authentication. It sends no org/project override, inference input or mutation.
No proxy, redirect, retry, credential file or provider-admin token is used.
Requests use bounded socket timeouts and incremental bounded response reads;
late responses cannot establish a fresh fence.

The implementation uses the existing pinned `httpx` transport and its normal
default User-Agent, as used by Groq's supported SDK. Root's synthetic comparison
found the same Groq request rejected with HTTP 403 through `urllib`, while normal
`httpx` returned the exact HTTP 401/invalid-key contract. HTTP 403 remains a refusal;
there is no browser impersonation, header spoofing or relaxed authentication gate.

Acceptance requires HTTP 401 and native JSON `error.code == "invalid_api_key"`.
Bare 401, missing/duplicate code, IP/organization errors, HTTP 403/404/429/5xx,
HTML, network failure, oversized/compressed response and stale observations all
refuse. Error messages and response bodies may reflect credentials; they stay in
memory and never enter receipts or failure outputs.

[OpenAI's error guide](https://developers.openai.com/api/docs/guides/error-codes)
documents several different 401 causes. Thus a generic failed request is
insufficient. Root separately binds the credential to actual signed-in
organization/project/key metadata, uniquely matches retained bytes in memory,
records any reviewed deletion and fresh UI absence, and observes the exact
before/after authentication behavior. No saved success flag authorizes closure.

[Groq's API reference](https://console.groq.com/docs/api-reference) documents model
listing. Its [error guide](https://console.groq.com/docs/errors) describes 401 as
invalid authentication but does not establish the exact machine code. Its
[security guidance](https://console.groq.com/docs/production-readiness/security-onboarding)
directs revocation through the Console. The native synthetic observation above
establishes the current exact code. No acceptance boolean or environment flag is
read; each exact retained credential must freshly produce the required denial.

This proves the exact captured capabilities cannot authenticate at the fresh
observation. It does not invent provider deletion timestamps, delete owner users,
certify unrelated keys, grant a broad admin key or establish future provider
availability. Root's actual identity/deletion evidence supplies attribution.

## Placement in the unchanged release gate

All foreign capabilities must be freshly denied before BoundScope is accepted
and before old Google principal/key isolation writes begin. Later cloud-isolation
checks and full retirement/serving closure reobserve every exact retained value
alongside existing Google UID/deletion, cloud principal, admission and SQL gates.
The foreign requests run in parallel and share the original 30-second oldest
observation deadline and native-read budget. A restored or unavailable capability
invalidates the fence; previous host receipts never refresh that clock.

The existing-owner resume path may have externally revoked foreign credentials
while old Google keys and cloud actors remain pristine. It requires native denial
of those foreign capabilities, not a false requirement that they remain active.
Every source/target identity, ownership, role, financial and preserved-data check
remains independently mandatory.

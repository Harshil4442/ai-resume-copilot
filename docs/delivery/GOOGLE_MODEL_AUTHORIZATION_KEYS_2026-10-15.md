# Google model authorization keys — 15 October 2026

This is a source correction, not credential retirement, model-access acceptance
or production deployment evidence. The actual retained credential census includes
Google authorization keys and older standard keys. It also includes historical
Groq and OpenAI credentials. Those providers need independent native retirement
evidence; this Google correction does not discard them or complete the global fence.

## Official API contract

[Google's Gemini key guide](https://ai.google.dev/gemini-api/docs/api-key)
distinguishes standard project keys from authorization keys bound to service
accounts. New AI Studio keys default to authorization keys. The current guide
also says explicitly restricted standard keys continue working; no blanket
September rejection is assumed here.

[API Keys v2 discovery](https://apikeys.googleapis.com/$discovery/rest?version=v2)
defines the optional `serviceAccountEmail` input and native metadata field. It
does not define a `keyType` field. Key strings are documented as encrypted and
signed opaque values; their prefixes are not ownership or authorization proof.

[Cloud's creation instructions](https://docs.cloud.google.com/docs/authentication/api-keys)
show REST creation with `serviceAccountEmail`. The operator needs key-creation,
service-account use and API-key-binding permissions. Solely Gemini-restricted
authorization keys do not need a blanket organization-policy exception. This
change adds no broad IAM role or policy exception.

## Preserved native checks

`native_fresh_fence.py` now admits only bounded, printable opaque strings for
native inspection. Known Groq/OpenAI prefixes refuse before any Google request.
For each Google candidate, official `lookupKey` must return a valid key resource
and matching parent. Native metadata supplies its UID and optional bound account;
the account's actual IAM email/UID is captured and joined to the retained principal
scope. An independent `getKeyString` read must equal the exact retained value.
Values stay in process memory and never enter receipts or failure messages.

Old key UID/issuer and principal UID/disabled-state checks remain mandatory.
The replacement key is explicitly bound to the actual isolated runtime account,
restricted solely to `generativelanguage.googleapis.com`, created after the
retirement cut and joined back through `lookupKey` before its numeric secret
version is published. Serving checks also reobserve its bound account UID/state
and exact API restriction. Changed bindings or restrictions invalidate the fence.

`OfficialWrites.create_model_replacement` now takes the actual returned
`NativeAccount`; the coordinator must pass `accounts[0]`. Unknown Create outcomes
retain the existing durable attempt and cannot replay. No new provider transport
or arbitrary resource routes are introduced.

Account/key metadata is not model-use permission or quota evidence. Before enabling
optional AI, observe actual model metadata and bounded generation with the new
authorization key under the valid model/expense policy. These official sources do
not establish a specific extra Gemini consumer IAM role; guessing an Editor or
Vertex grant is not an accepted substitute for native access acceptance.

## Existing-owner resume gate

The additional read-only `verify_prepared_owner_resume_pristine()` supports the
separate exact SQL owner-resume check. It requires all captured old identities
still enabled and old keys active, all replacement accounts/issuance artifacts
absent, and exactly the original enabled version 1 of each target setup secret.
It reads only current provider metadata, never secret values, and creates no
attempt marker. SQL independently proves unchanged source ownership and target
emptiness; neither side alone authorizes a resume.

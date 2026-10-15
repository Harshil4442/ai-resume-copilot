# Exact-version KMS pairing signer

Date: 2026-10-09. Implemented in an isolated source snapshot; no production KMS
call, credential inspection, provisioning, dependency/CI edit or deployment.

Version 2 supersedes the unintegrated first patch. Independent review confirmed
that the SDK's generated transport logged raw signing responses at DEBUG before
validation, including a usable signature from a checksum-rejected response.
The original snapshot and frozen patch remain retained as review evidence.

## Implemented boundary

`pairing_kms.py` implements actual Cloud KMS ES256 signing through the reviewed
`google-cloud-kms==3.18.0` protobuf request/response serializers and native unary
RPC bindings. It owns a TLS gRPC channel with
`grpc.enable_retries=0`, HTTP proxy discovery disabled and 16 KiB send/receive
limits. It accepts no injected channel, arbitrary host, ADC, environment key or
local signing fallback. Both SDK methods receive `retry=None` and a finite
native RPC deadline, capped by the remaining whole-operation monotonic budget.
Defaults are two seconds per RPC and five seconds for the operation; both
configurable limits must be positive, finite and at most ten seconds. Expired
deadlines withhold output, including after the native sign RPC returns.

The generated KMS transport/client and its payload-logging interceptor are not
constructed. Native `GetPublicKey` and `AsymmetricSign` bindings use the exact
SDK protobuf wire contract and a logging-free API-core error/deadline wrapper
with `default_retry=None`, `default_timeout=None` and `client_info=None`; each
call additionally passes `retry=None` and its finite deadline. No global logger
level or environment setting is mutated, and DEBUG is supported. A logging
configuration change during an RPC cannot introduce that absent interceptor.
Sanitized provider failures are raised outside the exception handler, so their
cause/context graphs also omit raw provider-error content.

The operator pin contains an exact `CryptoKeyVersion` resource (a positive
numeric version, never a CryptoKey alias/primary), explicit official global or
matching locational endpoint, SOFTWARE/HSM protection level and canonical
public P-256 JWK. The constructor freezes its public coordinates/SPKI so caller
mutation cannot replace the trust identity. A locational endpoint must match
the resource location; the global endpoint routes by the resource location.
[Official endpoint behavior](https://docs.cloud.google.com/kms/docs/reference/service-apis-overview).

Every sign operation first calls `GetPublicKey` for the exact version, without
requesting another public-key format. It requires the matching version name,
`EC_SIGN_P256_SHA256` algorithm, configured protection level and valid PEM
CRC32C. It parses an actual P-256 public key and compares its exact SPKI to the
operator pin. Any disagreement stops before `AsymmetricSign`.
[Public-key API](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys.cryptoKeyVersions/getPublicKey).

The message is derived internally from one frozen full-schema JSON payload and
the existing v2 canonical domain. The signer sends only SHA-256(message) plus
CRC32C of those digest bytes. It never accepts caller-supplied digest bytes,
hash algorithm, resource, canonical domain or `data` through the endpoint
signer API. The sign reply must name the exact version, report
`verified_digest_crc32c is True`, match the configured protection level and
carry a present strict unsigned-32-bit signature checksum matching the received
DER bytes. An absent wrapper is not equivalent to a valid checksum of zero.
[Asymmetric signing API and integrity fields](https://docs.cloud.google.com/kms/docs/reference/rest/v1/projects.locations.keyRings.cryptoKeys.cryptoKeyVersions/asymmetricSign).

Cloud KMS returns ECDSA DER. The adapter decodes its two scalars, checks P-256
range and canonical DER round-trip, converts them to fixed-width 32-byte
`r || s`, and verifies the resulting ES256 signature locally against the pinned
key and the exact domain-separated message before returning unpadded base64url.
Valid high-S signatures remain interoperable with the existing WebCrypto
protocol; signatures are not replay identities. CRC32C detects transmission
corruption, while TLS and local pinned-key verification establish the separate
authentication/signature checks.
[KMS signature encoding](https://docs.cloud.google.com/kms/docs/create-validate-signatures).

## Endpoint-specific API

```python
pin = KmsEs256Pin(
    version_name=operator_approved_version,
    endpoint=operator_approved_official_endpoint,
    protection_level=operator_approved_protection,
    public_key=operator_verified_public_jwk,
)
boundary = KmsEs256Boundary(pin, access_token=trusted_short_lived_runtime_token)
auth_signer = KmsCandidateAssertionIssuer(
    boundary, issuer=fixed_recent_auth_issuer, now_ms=trusted_server_clock,
)
claim_signer = KmsPairingClaimIssuer(boundary, now_ms=trusted_server_clock)

assertion_envelope = auth_signer.sign(server_derived_candidate_assertion)
claim_envelope = claim_signer.sign(server_derived_device_claim)
boundary.close()
```

`sign(payload)` returns exactly `{"payload": frozen_payload,
"signature": raw64_base64url}` and performs no enrollment or application
action. It accepts only every exact field of its endpoint schema, canonical
types/UUID representation and the fixed `hirewiz:pairing-only` audience.
Candidate assertions additionally require the configured fixed issuer,
`confirm_pairing`/`revoke_device`, a permitted recent-auth method, explicit true
confirmation, current validity and authentication within 60 seconds. Device
claims require `hirewiz-pairing` / `device_identity` and current validity within
the existing five-minute bound. Schema and audience restrictions also apply at
the reusable private crypto boundary. No generic digest/fill/JWT signing API
is exposed.

These are server seams, not HTTP endpoints. A recent-auth service must derive
the assertion from independently verified current account/session/challenge
state and consume it through the actual pairing service. It must never sign
browser-supplied owner/generation/authentication declarations. The claim seam
implements the existing `ClaimIssuer` and belongs behind the coordinator's
confirmed native Commit and fresh fence checks. The coordinator's final fence
still gates delivery after signing. A timeout/lost response emits no envelope;
it is not permission to retry an ambiguous pairing mutation or mint a claim on
receipt replay. The signing adapter itself has no current-registry or restore
authority and does not substitute for these checks.

Use separate approved versions and service principals for authentication
assertions and pairing claims where the deployment policy requires that trust
separation. KMS IAM cannot restrict signatures by message audience/domain; that
restriction is enforced by the server schemas and authenticated call paths.

## Precise runtime and IAM requirements

- Before integration, approve and pin `google-cloud-kms==3.18.0` in the main
  dependency lock. This work installed only that wheel with `--no-deps` into the
  isolated `.sdk` target; it did not alter the main environment. The proof
  manifest records the actual grpc/api-core/auth/CRC/crypto dependency versions.
  An unreviewed KMS SDK version fails closed and requires fresh verification.
- An operator must provision an ENABLED ASYMMETRIC_SIGN version using
  `EC_SIGN_P256_SHA256`, verify its public JWK/SPKI and protection/location, and
  configure that exact immutable version together with verifier trust. The
  runtime may not create/import/rotate/destroy keys or discover a replacement
  from a primary alias. Rotation is a separate reviewed change of signing and
  verifier pins, with explicit old-claim/assertion lifetime policy.
- The runtime principal needs only
  `cloudkms.cryptoKeyVersions.useToSign` and
  `cloudkms.cryptoKeyVersions.viewPublicKey` on the specific CryptoKey, using a
  custom role or the signer plus public-key-viewer roles. IAM policies cannot
  attach to a CryptoKeyVersion, so the code's exact-version pin is necessary
  even with key-scoped permissions. `signerVerifier` adds unnecessary verify
  permission. Require separate operator/key-management permissions, audit logs
  and reviewed account/resource policy.
  [Official permissions and IAM attachment scope](https://docs.cloud.google.com/kms/docs/reference/permissions-and-roles).
- A trusted credential boundary supplies a short-lived OAuth access token for
  the intended Cloud Run service identity and KMS API scope. The signer neither
  loads a service-account key/ADC nor performs token refresh or metadata-server
  calls. Static gRPC call credentials avoid an unbounded refresh inside the
  signing RPC. The production credential boundary must independently verify
  principal/scope/expiry and obtain replacement tokens within its own bound;
  construct/close a newly pinned signer when replacing a token. Tokens stay
  server-side and never enter logs, artifacts, frontend or extension messages.
- Permit only TLS egress to the reviewed official endpoint and resource
  location. No redirects, proxy discovery, caller-selected transports or
  automatic mTLS/endpoint selection are allowed. `retry=None` is required
  because SDK RPC wrappers otherwise supply retry policies; the owned channel
  also disables gRPC transparent/service-config retries.
  [Generated SDK retry defaults](https://raw.githubusercontent.com/googleapis/google-cloud-python/main/packages/google-cloud-kms/google/cloud/kms_v1/services/key_management_service/transports/base.py),
  [gRPC retry behavior](https://grpc.io/docs/guides/retry/).
- Successful production proof must verify the real service identity, exact key
  resource/public identity, IAM, network policy, deadlines/audit records and
  current pairing/authentication/restore fences before any real claim is
  enabled. No environment flag, local private key or fixture can establish
  those conditions. Existing production assertion/claim factories remain
  unavailable in this patch.

## Evidence and limits

The test uses the actual KMS 3.18.0 protobuf types/serializers/deserializers and
native unary bindings plus the API-core wrapper through an intercepted
`grpc.Channel`. It asserts the owned channel options, exact native requests,
digest/CRC, omitted data, resource routing and finite deadline at the native
call boundary. Synthetic P-256 keys generate provider replies only in tests.
ADC and real insecure/cloud channel construction are denied; no KMS, emulator
or shared database is contacted.

The fixture also forbids `grpc.intercept_channel`. Enabled-DEBUG and
enable-during-RPC logging cases cover valid, expired, checksum-corrupt and
RPC-error replies. Even the deliberately rejected native signature is locally
shown to be usable cryptographically, while captured logs, sanitized exception
args/context/cause and standard traceback text contain no signature, token,
digest or candidate payload. This verifies the removed logging path instead
of assuming a default INFO level or checking DEBUG once before a call.

Adversarial cases cover wrong version, algorithm, protection, public key/curve;
absent/bad checksums and verified flags; checksum-valid corrupt DER, bad scalars
and trailing bytes; wrong-key and double-hashed signatures; schema/audience/
issuer/type/lifetime attacks; input/key mutation; unsupported key aliases and
endpoints; native timeout/unavailable replies with one sign attempt; expiry
after a valid reply; explicit locational routing and environment overrides.
An actual existing `PairingService` test consumes the KMS assertion, returns a
KMS claim after its local fixture Commit and refuses duplicate completion
without another signer call. That SQLite fixture proves the seam, not cloud
authority retention or real candidate authentication.

The proof manifest/JUnit record the frozen patch, static checks and focused
counts. This is implementation and native-SDK contract evidence. It is not
production KMS/IAM/KMS audit, cloud network or real credential proof; no live
operation is asserted as completed.

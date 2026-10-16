# Native recent-password pairing boundary

This isolated slice connects the actual `PasswordReauthService`, registered-identity reader,
`GcpPairingCoordinator` and existing `PairingService`. It creates no HTTP route, credentials,
cloud resources, subjects, account bindings or sessions. Default authority, witness,
assertion/claim issuers and password-signing storage remain unavailable. Pairing remains
identity-only; no employer operation, file, approval, submit, usage or opening capability
is created by this boundary.

Baseline is tracked parent `3b96c795bf6d87f82af394a3ed99b754fe0dbfa2`; the parent's
reviewed SQL hash type-narrowing repair is retained. No parent/cloud source was mutated.

## Exact connected path

1. The transport must supply a trusted `CandidateWebSession`, resolved server-side from
   the retained account/session mapping. Browser bodies, numeric bearer claims or email
   equality cannot construct that context. `PasswordReauthRequest` carries only the
   operation, explicit confirmation, exact challenge/nonce and recent password.
2. The existing bounded native SQL credential reader verifies the current password. The
   new `CandidateChallengeProof` contains no password and keeps the exact nonce private.
3. `GcpPasswordCandidateBoundary.read` runs a typed native read. The account binding,
   absence of its tombstone, registration events, subject lifetime/principal/auth-generation,
   retained web session and exact independent pairing session must agree. The candidate
   challenge must be unconsumed, current, nonce-matched, same owner/session and current epoch.
   Generic pairing transactions still cannot read the three new identity namespaces.
4. The service builds the unsigned assertion from that server snapshot. Before KMS it
   calls the explicit signing-admission port. `GcsPasswordSigning` performs **one**
   generation-zero create at `authority-password-signing/<authority-hash>/<challenge-id>.json`.
   The marker binds the exact challenge/assertion digests, nonce commitment, account/session,
   subject/principal/auth-generation, full resource/UID/incarnation/epoch pin and at most
   60-second assertion interval. It contains no password/hash/raw nonce/signature.
5. Only the fresh POST acknowledgement, strict metadata and generation-bound exact bytes
   can yield a receipt. Existing objects, malformed replies or unknown POSTs are never
   adopted, reconciled into permission, overwritten or automatically retried. The native
   immutable `pairing_password_signing_attempts/<challenge-id>` claim is then created after
   revalidating current identity/challenge inside **each** native attempt. Only this
   invocation's definite fresh Commit acknowledgement permits signing. An existing or
   ambiguous native claim yields no signing permission, even if a later read finds it.
6. The supplied reviewed KMS assertion issuer signs once outside native transactions.
   The private acknowledged admission is tied to the exact unsigned assertion, context,
   nonce and operation. It is not a serializable/restartable permission.
7. The typed consuming dispatcher rechecks the protected marker/current fence after
   signing, then enters the existing coordinator's protected intent → attempt marker →
   native invocation claim → actual core lifecycle. All generic and consuming native
   attempts revalidate identity/session/account lifetime and the exact signing claim.
   The existing core additionally checks current request/device ownership, exact challenge,
   pinned signature, recent authentication and single assertion consumption. Retries are
   restricted to definite `ABORTED` and preserve allocated IDs and the same assertion.
8. Only a confirmed native mutation plus fresh fences can return the existing strict
   redacted confirmation/revocation result. Unknown outcomes return unavailable/status-only;
   no signed assertion, nonce or credential is returned. Intents record only the signing
   claim digest/reference; native receipt status verifies that protected evidence. Ordinary
   v1 intents omit the optional field entirely, preserving existing canonical receipt bytes.

## Unknown-phase distinction

A first unknown protected create may have persisted nothing. That request stops before
native signing admission and KMS. A separate explicitly initiated request can sign only
if its own later generation-zero create obtains a fresh acknowledgement. It cannot infer
absence, adopt the unknown attempt or retry its POST. If the original marker exists, any
later create receives a conflict and signing is denied, including from a new coordinator.

Once the protected marker exists, every later failure consumes that challenge's signing
attempt conservatively: native admission response loss (whether native row persisted or
not), signing failure, post-sign lifetime loss, and ambiguous consuming commits cannot
produce a second assertion. A fresh challenge, genuine recent authentication and fresh
admission are required. Process-local maps/latches are not durable restart authority;
the stable challenge marker is the cross-process signing gate. Receipt status can report
historical committed evidence; it cannot recover authority, a signature or a claim.

## Concrete next writer/provisioning seam

The reader is connected, but production has **no protected writer** for the account and
web-session registration records it requires. The next vertical must implement a separate
operation-restricted `GcpPasswordLifetimeCoordinator` with server-only typed commands:

- `bind_password_account`: trusted numeric account → immutable new account-binding UUID →
  already registered active subject UUID/principal. Commit `PasswordAccountBinding` and
  the exact `PASSWORD_ACCOUNT_BOUND` event; the event payload is the binding excluding
  `registration_event_id`. Never reuse the binding/subject lifetime after deletion or
  account recreation. Existing-subject bootstrap requires reviewed provenance; SQL/email
  lookup is not independent registration proof.
- `create_password_web_session`: new server session UUID, exact bound active subject,
  principal and current auth-generation, bounded issue/expiry. Commit `RetainedWebSession`,
  the exact `PASSWORD_WEB_SESSION_CREATED` event (excluding `registration_event_id`),
  and the matching `pairing_sessions` projection. Give the authenticated server transport
  only that retained context; do not replace it with a browser-declared session UUID.
- `revoke_web_session`, `advance_auth_generation`, `tombstone_password_account` and
  subject deletion: independently retain monotonic denial/lifetime effects and events
  before acknowledging remote logout/password reset/deletion or updating SQL mirrors.
  Unknown/unavailable authority keeps execution blocked and the administrative action
  pending. Concurrent registration/revocation, stale sessions and password-change races
  need real native proofs. Writers must never journal a password, hash, bearer or cookie.

Each writer needs a preallocated command/event plan, protected replayable **lifetime
effects** (not merely input digests), fresh external witness, native read-before-write/CAS
and own Commit acknowledgement. Restore must replay complete account/session/subject
lifetimes and permanent tombstones together with signing/invocation consumption and pairing
projections before reopening a pinned incarnation. Existing pairing command intents alone
do not reconstruct those effects. Missing partitions, old SQL restoration or a lost marker
must never bootstrap a fresh active mapping. No fixture registration proves this gate.

Operators must provide and verify the exact Firestore resource/UID/incarnation, protected
GCS retention and IAM separation, verified fresh closed-cut witness, pinned SDK/runtime,
server session authentication and fixed KMS version/public key/issuer roles. Signing-marker
storage requires create/get only, without overwrite/delete authority; native transaction
and protected read/write permissions must be tested against actual policies. The reviewed
KMS boundary's IAM/deadline/CRC/debug-disclosure requirements remain mandatory. This slice
does not manufacture any of those policies or enable an environment-selected factory.

## Bounded evidence

New tests use only fresh uniquely named databases on the authorized root-owned
`127.0.0.1:58877` Firestore emulator, project `hirewiz-local-authority`, AnonymousCredentials,
synthetic principals/passwords and intercepted Storage HTTP. No reset/delete, real cloud,
provider, candidate or KMS call occurs. Synthetic registration events are operator fixtures,
not a deployable lifetime writer or IAM/retention/restore proof.

The connected lifecycle includes actual password SQL verification, native signing admission,
actual core confirmation, device completion and password revocation. A further case connects
the reviewed KMS SDK/protobuf boundary to that same native lifecycle and claim signing.
Adversarial cases cover identity changes before admission/after signing/after definite abort,
unknown native outcomes with/without persisted rows, protected-create phase distinctions,
fresh-fence closure, concurrent coordinators, corrupt/unknown Storage acknowledgements,
namespace denial and canonical v1 receipt compatibility. Static and focused results are
recorded separately in the freeze manifest; none constitutes production configuration proof.

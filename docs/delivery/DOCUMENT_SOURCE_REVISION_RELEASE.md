# Document worker source revision and daily definitions maintenance

## Status and scope

This is a prepared release path, not a deployed or qualified worker. It integrates
the reviewed v2 development evaluator and the bounded PDF reading-order/refusal
changes. No image digest, Build ID, Job execution, native acceptance, production
pin or activation has been invented. Existing V2/V3 constants, policy bytes,
payloads and historical journals remain unchanged.

The separately pinned policy is
`infra/document-processing/policy.source-revision.json`, SHA-256
`43af7715702355491413b40544a439529caa7e668a3514229173a4eef35de4e1`.
Only the source hashes for `processor.py` and `parsing.py` differ from frozen V3.
The 21 source inputs, 20 installed runtime files, wire protocol, source/edit
bounds, renderer/font rules, sandbox, resource limits and IAM requirements stay
the same. The policy's safety semantics remain V3; its separately versioned
native release proof is **V4, 16 required cases**.

The five additional generic PDF controls cover row-painted independent columns,
late-painted right-aligned dates, spanning-column refusal and earlier unaligned
section refusal, plus font-relative word spacing at small/large sizes. They run
after all eleven unchanged V3 scanner, source, font,
DOCX and sandbox controls. The local engine checker also retains all existing
source/scanner/clamd/layout checks and adds these five controls. This narrow
regression corpus does **not** establish full résumé quality: the separate
prior 5ea development assessment passed 23/40 strict reading-order cases, with
17 failures; the full cohort has not been rerun for the new spacing source. Full
development/held-out extraction, native format output and human
quality qualification are still required; do not relax expected facts to pass.

## Two exact Build kinds

1. `native_cloud_build_source_revision_v1` creates the full worker from the exact
   committed Git source. The archive contains the 21 reviewed inputs and the new
   policy. Docker source labels, Git commit and the digest of the actual release
   helpers are bound into the Build request. Native readback checks the builder
   account, two exact Build/push steps, successful result, immutable image,
   generation-bound archive and source bytes. Dirty or uncommitted source cannot
   authorize a Build. This replaces the parser once; it is not a daily rebuild.
2. `native_cloud_build_source_definitions_v1` is the daily successor. Before
   upload or Build intent, fresh current-worker/native proof must establish the
   actual current immutable parent, this policy/runtime map, V4/16 proof and the
   unchanged renderer/font identity. Its recipe is the frozen V3 definitions
   recipe with exactly the two parent-image literals replaced. Only antivirus
   definition files and the explicitly declared Git/qualification metadata can
   change. The child still needs its own exact Build, local engine check,
   singleton inventory Job and singleton V4 corpus Job before promotion.

Unknown policies or parents are refused before a Build. A new-profile scheduled
refresh cannot select V2, old Courier V3 or a daily full-source build. The child
starts from the observed current image, so registry storage/layer retention still
needs ordinary operator management; this change does not claim storage cleanup.

The renderer must freshly report `LibreOffice 25.2.3.2 520(Build:2)` and font
manifest SHA-256
`87b2c806de6d5e3f528e393f3ad6b2a223f89e21f72f811bc96d8d21cc2cc526`.
A different result requires review, not an automatic pin update.

## Native acceptance and promotion

`SourceProbe` adapts the established candidate Job and maintenance transition
controllers. Inventory and corpus remain separate, scoped singleton Jobs with
the existing service identity, gen2 sandbox, fixed resource/time bounds and zero
task retries. The actual Job/Execution templates, UIDs, runtime bytes, unique
bounded stdout receipt, definitions freshness and full 16-case proof are read
back. Historical readback reconstructs the precise probe from its retained Git
commit; retained evidence cannot authorize new work after a helper/source edit.

Promotion requires the existing reviewed application context: exact current
application source, Build, CI, UID/template/traffic and wire agreement. It closes
the three caller admission paths, changes the worker, freshly verifies it, pins
all callers to the exact image/policy/renderer/font, and restores only the
captured flag states (including absent or false values). Other environment,
secret references, resources, financial/AI/checkout/submission gates and service
identities stay protected by existing exact readback. This is not a claim that
synchronous in-flight callers have drained; existing safe refusal remains.

Every upload/create/run/PATCH keeps its established exclusive intent and one-send
semantics. Unknown acknowledgement stops the operation; it must be reconciled
read-only against that exact journal/resource. A create-only
`native-proof/transition.started.json` also binds the chosen transition path, so
a lost outcome cannot be retried under another directory. No automatic replay,
new nonce or overwritten journal is a recovery strategy. The completed V4
history reader joins the Build/source/probe/acceptance/transition identities;
history is never a substitute for fresh current native authority. Prior Jobs and
evidence are retained rather than incorrectly claiming V2-only cleanup works for
this profile.

## Prepared operator commands — do not run until separately reviewed

Use a clean committed checkout, the locked backend runtime and a new private
output directory. Commands below describe future cloud actions; authoring this
document did not execute them. The actual current application source must first
be accepted by the existing application registry; never invent its Build/CI pins.

Candidate Build plus local engine check only (no native Job or promotion):

```sh
backend/.venv/bin/python backend/scripts/document_refresh.py \
  --mode full-source-candidate --source-revision --publish-candidate \
  --output-directory /absolute/private/new-source-candidate
```

Full source qualification and rollout, including actual native Jobs and reviewed
application/consumed-history verification:

```sh
backend/.venv/bin/python backend/scripts/document_refresh.py \
  --mode full-source-refresh --source-revision --publish-candidate \
  --reviewed-application-context \
  --consumed-history-bundle /absolute/private/exact-consumed-bundle \
  --prior-history-directory /absolute/private/retained-history \
  --output-directory /absolute/private/new-source-release
```

Once that exact profile is actually serving, ordinary maintenance selects its
definitions-only successor from fresh observed current identity:

```sh
backend/.venv/bin/python backend/scripts/document_refresh.py \
  --mode refresh --publish-candidate --reviewed-application-context \
  --consumed-history-bundle /absolute/private/exact-consumed-bundle \
  --prior-history-directory /absolute/private/retained-history \
  --output-directory /absolute/private/new-definitions-refresh
```

The existing workflow adds `source_revision=true` only for an explicitly chosen
`full-source-refresh`. Regular scheduled `refresh` uses the typed child branch
after observing the new serving policy. Native CI invokes
`document_test_image.py --platform linux/amd64 --source-revision`; only actual
successful CI/native executions can supply acceptance evidence.

## Remaining evidence before any release claim

- Independent source review and exact-source CI, including real native engines.
- Actual full-source Build/archive/image provenance, native inventory and V4
  corpus, followed by current serving-worker and all-caller readback.
- A genuine definitions-only child Build and native acceptance before claiming
  scheduled maintenance works for this source profile.
- Separate repairs/assessment of the remaining development reading-order and
  word/paragraph extraction failures, then full v2 corpus and human qualification.
- Existing financial, canary/shadow, invoice and commercial acceptance remain
  governed by their own requirements. No flags or commercial routes are opened.

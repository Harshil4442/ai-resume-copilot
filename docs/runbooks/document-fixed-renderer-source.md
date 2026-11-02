# Fixed-renderer source maintenance

The current source-revision production Build uses
`infra/document-processing/source-fixed-renderer.Dockerfile`. Its two stages
inherit immutable image `bbfad0c7faa2acf0ef93d13df300a9ac34cb16a792fee04d873aa994ec3afb94`.
The recipe preserves that image's renderer, dependency libraries, fonts,
fontconfig rules and generated caches, renderer manifest and scanner binary
manifest. It refreshes official ClamAV definitions in a nonce-bound stage and
copies only the reviewed Python runtime files. It performs no package install,
font-cache refresh or renderer-manifest regeneration.

The closed current policy inventories 22 source inputs, including both recipes.
The runtime inventory remains 20 files. Public CI executes the unchanged public
Dockerfile and binds all 22 contextual inputs through its source labels and
report. A passing public build is separate from production renderer acceptance.
The production Build must independently bind the exact recipe, immutable parent,
committed clean Git revision, helper qualification and generation-bound archive.
The original 31 local checks, 17 native cases, nine agreeing clean scan receipts
and exact renderer/font pins remain mandatory before the existing transition.

The original 21-input policy `6a080773…`, source `41c57ff…` and helper
qualification `949cbfed…` remain historical read-only identities. They cannot
select new Build, Job or transition authority. In particular, workflow
41065248592 produced successful Build `f2dbba80-2c48-4e49-b1cf-8f30e4be109a`
and image `245a2479…`, then refused its renderer before local proof or native
Jobs. Its exact retained five-file journal can establish abandonment only after
fresh exact Build/archive readback, both canonical corpus and inventory Jobs
returning 404, and unchanged current serving/caller controls. Unknown reads or
any Job presence refuse. The original journal and nonce never replay; only a
fresh current-profile attempt may follow. This disposition creates no native
acceptance, serving change, claim or cost release.

Source preparation supplies no native approval. Integration still requires a
fresh app/operator qualification, exact-head CI and Git Build tuple, followed by
one fresh owned production Build and all existing native gates. Retain original
failed-run artifacts; no legacy rescue flag substitutes for these checks.

## Historical completed-source decoder

The ordinary history selector must recognize the exact completed
41108319542/41119586258 pair before retaining a new attempt. Its old canonical
operator hash includes the maintenance workflow itself. The current workflow
has changed, so executing that reader against current HEAD correctly refuses;
that refusal does not mean the historical completion is absent.

History normalization therefore invokes only `--mode select-history` from a
fresh private Git worktree pinned to
`f5e9ca03383afac4c6cda17134bfc854c3ba8116`, tree
`7e92b98914ae1887951ed2667b0f9fba25d803ab`. Before import, the workflow verifies
the clean whole tree, every local importable source, original three-file
operator hash `3325a9f173c223d433fbfc51a1a7545761ca4e6aeead993fcc65c94e86e57871`,
original fourteen-helper qualification `61b313bc…10ae50`, and identical locked
dependencies. The frozen reader and CLI must also equal their current bytes.
It uses the actual current workflow environment without changing GITHUB_SHA.
This checkout is a historical decoder, never the current mutation operator.

That exact role rereads GitHub metadata/archive bytes, the generation-bound old
claim and historical Build/source archive, then checks the original completed
receipt as identity. It does not call current-worker preflight/freshness,
NativeAcceptance construction, claim creation, Build/Job creation or Run,
promotion or PATCH. An expired original worker or later accepted definitions
descendant does not alter historical decoding. Missing/expired artifacts or
changed original source, archive, claim or receipt still refuse.

The three-history bound stays unchanged. Normalization preserves original
input trees and copies the exact completed history plus all four raw failed
5080 Build files. It excludes the duplicate original only after genuine
completed consumption is proved. The current source/helpers then independently
verify the current worker/callers and failed-Build disposition before allocating
a candidate. No old acceptance becomes current, no old mutation guard is
relaxed, and a fresh directory grants no replay authority.

## Explicit recovery of an expired source V4 predecessor

Ordinary refresh continues to require a current unexpired predecessor. If that
source profile has already expired, the manual workflow's
`recover_expired_source=true` selects the existing definitions-only pipeline:

```sh
backend/.venv/bin/python backend/scripts/document_refresh.py \
  --mode refresh --publish-candidate --reviewed-application-context \
  --recover-expired-source \
  --consumed-history-bundle /absolute/private/exact-consumed-bundle \
  --prior-history-directory /absolute/private/retained-history \
  --output-directory /absolute/private/new-definitions-recovery
```

The mode requires the exact currently serving source V4 policy, immutable
Build/archive/source, physical worker identity/IAM and original complete
17-case/nine-scan evidence. It records an `ExpiredSourcePredecessor` with
processing authority false. That type is rejected by the native-acceptance
boundary; neither saved timestamps nor an expired image become fresh acceptance.
V2 recovery flags, full-source builds, other recovery/resume modes and scheduled
dispatches cannot select this path.

A new nonce-bound official definitions layer, immutable Build/image, all31 local
checks, singleton inventory Job and full17-case corpus Job remain required.
Candidate definitions must be younger than24 hours. After closing all three
callers, the transition rechecks the unchanged expired predecessor before moving
the worker. It then verifies the fresh successor and exact caller pins and
restores only captured flags. The original three-day processing refusal stays
unchanged. Unknown outcomes retain the original journals and require exact
read-only reconciliation; a new directory does not authorize replay.

Worker22/runtime20/policy bytes and the fourteen-helper inventory remain intact.
Changed maintenance helper bytes receive a new exact committed-source helper
qualification and genuine CI/Build evidence. Historical source/helper hashes and
native receipts remain historical. This documented source path itself proves no
deployment, scanner renewal, processing admission or quality-gate completion.

Definition recipe version2 temporarily makes only `/var/lib/clamav` writable by
the updater's UID100/GID101. After a successful `freshclam`, it restores root
ownership, directory mode0555 and file mode0444 before the final immutable layer.
The final runtime user remains65534. The commands form one failure-stopping chain;
an updater failure produces no accepted image. Historical readback recognizes the
exact old committed helper hashes and reconstructs their original recipe, rather
than comparing an old archive with the new permission fix.

A retained four-file upload/Build-intent/Build-ACK journal is admissible only
after fresh readback proves its exact Build failed before push, with a nonzero
first-step exit, queued unstarted push, no image result, exact original request,
source generation and archive bytes. Its disposition preserves all four original
files and the unresolved provider expense; it supplies no image, native identity
or acceptance. Only after that proof and the current worker/caller join may the
pipeline allocate a different nonce. Missing, running, successful, changed or
partially observed Builds remain reconciliation-only.

Recovery continuity remains explicit. If the original singleton native Jobs
actually completed and their original candidate journal has no transition intent,
the existing `document_source_release.promote` entry can freshly verify that same
Build/Job/log evidence and begin its first transition without another Build or
Job. Its create-only `transition.started.json` prevents using another transition
directory after an uncertain start. This is an operator entry, not a generic
CLI resume option.

For an interrupted transition, `--mode reconcile --transition-journal
/absolute/private/original/transition --output-directory /absolute/private/new-readback`
only reads the original acknowledged LROs and reports started operations lacking
ACKs. It performs no restoration and grants no replay permission. An unknown
upload/Build/Create/Run outcome needs exact source-object/Build/Job/Execution
readback; a partially changed worker or caller needs separately reviewed
original-journal reconciliation and remaining-only restoration. No generic
automatic continuation is claimed for those cases. Preserve the original flags,
handles and once markers; never rerun the expired-source command in a fresh
directory to escape ambiguity.

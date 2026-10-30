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

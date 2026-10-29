# HireWiz unified credit quality evaluation packet

This folder contains 200 frozen **draft synthetic resume and job pairs**, not approved reference outputs or a passed qualification. No real candidate data, provider calls, reviewer scores, statistical bounds or native-rendering results were generated for this packet.

The corpus covers five roles, four seniority levels and four intended formats: PDF, DOCX, TeX and TeXZIP. Risk groups overlap: long documents, tables, multiple columns, unusual fonts, numeric claims and incomplete approved evidence. There are 40 development pairs and 160 held-out pairs, stratified by role and format. Every declared important cohort has at least 30 held-out cases. These are deliberately limited English-language draft inputs; the packet does not establish worldwide, multilingual or all-profession coverage.

`source_recipe` is a reproducible specification of the desired native source. The frozen corpus directory contains recipes; the separate [materializer](MATERIALIZATION.md) now produces actual native files in an external evaluation directory. All 200 sources were materialized locally with exact fonts, complete-content checks and representative visual inspection. This does **not** replace scanning and inspecting every file with the application's approved document runtime. Source hashes, extracted text, rendered pages and full visual-review receipts must still join the imported evidence bundle. A format label alone never counts as format-preservation evidence.

The manifest freezes each pair digest, split, corpus digest, protocol and rubric. Rebuilding into an existing packet is refused. A material change requires a new packet version and renewed approval; never move an inconvenient held-out case into development after viewing its output.

## Commands

Run from `backend`:

```sh
.venv/bin/python scripts/unified_quality_packet.py validate
.venv/bin/python scripts/unified_quality_packet.py evaluate-evidence --evidence /absolute/path/to/approved-run-evidence
.venv/bin/python scripts/unified_quality_packet.py make-blinded --evidence /absolute/path/to/approved-run-evidence --destination /absolute/path/to/new-review-packet
```

`validate` checks only the frozen draft input packet. Its report always says quality is not evaluated and authorizes_release is false. `evaluate-evidence` exits 2 when evidence is absent, incomplete, contradictory or inconclusive. Even complete evidence requires independent release review; this tool never enables a production route.

## Stage checklist

- [x] Draft 200 synthetic pairs and declare their intended source layouts and truth invariants.
- [x] Freeze input hashes, corpus split, protocol and scoring rubric.
- [x] Provide empty reference, human-review, adjudication and statistical-analysis templates.
- [x] Provide offline native-file signature, artifact-hash, output-contract and evidence-consistency checks.
- [ ] Independently review the draft inputs, supported cohorts, rubric and statistical design before use.
- [ ] Materialize and inspect every native source with the approved runtime; bind immutable hashes.
- [ ] Select, execute and approve a valid reference route. Existing broken outputs cannot be the baseline.
- [ ] Authorize a funded evaluation allowance and the exact prompt/model/validator versions in the existing durable execution runner.
- [ ] Generate actual reference and candidate outputs through that runner, retaining authorization, execution-hold, usage and expense receipts. Import them; do not use a raw SDK or create a separate ad hoc spending ledger.
- [ ] Package blinded A/B outputs. Keep the coordinator directory private and inspect the reviewer copies for identity leaks before distributing them.
- [ ] Obtain two independent human reviews per output and third-person adjudication of disagreements.
- [ ] Obtain the independently reviewed powered paired analysis for each important held-out cohort and metric.
- [ ] Run evidence validation, resolve every critical defect, and obtain independent release approval for the exact route digests.

## Imported evidence

Copy the templates into a separate evidence directory and replace blanks only with actual evidence. Never mark a template complete merely to make a check pass. The `bundle.json` binds this manifest and protocol and references approval, reference registry, review CSVs, blinding map and external statistical analysis by relative path and SHA-256. Paths must remain inside the evidence directory.

Each case needs the immutable native input and its validation receipt; actual reference/candidate JSON outputs; and funded-run receipts binding input/output digests, run ID, execution hold, provider/model/prompt version, usage provenance, funding authorization and retained expense ledger export. A tailoring result additionally needs native output, reviewed-artifact hash and complete structure/render review. Required native receipt fields are `source_sha256`, `artifact_sha256`, `structure_pass`, `render_pass`, `reading_order_pass`, `links_pass`, `format_fidelity_pass`, `immutable_source_pass`, `extracted_text`, `rendered_pages` and `visual_review` with reviewer ID, review time and every reviewed page hash. Signature checks are only a first filter; they never replace these receipts.

The coordinator mapping preserves which original hash each blinded label represents. Reviewers receive presentation copies with only operational envelope metadata removed; original deliverable bytes remain unchanged. The coordinator must check for identity leakage inside document content and metadata. No automatic check claims that blinding succeeded.

A statistical report must bind the exact reviews, adjudications and held-out cases; name its primary method reference, analyst, analysis code and approved powered design; and supply every cohort/metric's paired count, mean difference and one-sided 95% lower bound. Zero is the maximum tolerated degradation. The validator checks the reported estimate against adjudicated score arithmetic but deliberately does not invent confidence bounds, sample-size justification or reviewer judgments.

## Limits of this tool

A checksum detects changes; it does not authenticate a human, provider invoice or source-of-truth database export. These artifacts still need independent verification against the authorized runner and review process. Text/schema checks cannot prove semantic factual support or visual quality. The packet contains no real provider output and supports no noninferiority claim until the required work is completed.

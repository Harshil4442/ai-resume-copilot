# Offline native source materialization

The frozen packet remains unchanged. This command creates synthetic native inputs in a new, separate directory and binds every file to its original case hash. These sources are test materials, not approved references or passed format-preservation evidence.

From `backend`:

```sh
.venv/bin/python scripts/materialize_quality_sources.py --destination /absolute/path/to/new-native-packet
```

Use repeated `--case synth-001` arguments for a subset. `--fonts /absolute/path/fonts.json` accepts a JSON object mapping the exact family names `Times New Roman` and `Courier Prime` to locally available TTF files. The font's own family metadata must match. The tool does not download fonts or accept substitutes. Ensure font licenses permit the intended use; font binaries are not copied into the packet.

An unmodified Courier Prime family is pinned under `backend/evals/assets/courier_prime`, with the upstream OFL license, copyright, immutable source URLs and SHA-256 provenance. From the backend directory, a local font-map file can use `"Courier Prime": "evals/assets/courier_prime/CourierPrime-Regular.ttf"` and a separately supplied exact Times New Roman path. Font acquisition is separate from offline generation; the materializer never downloads dependencies or installs fonts into the operating system.

The implemented recipes preserve the full frozen section content and order, numeric claims, synthetic identity, portfolio hyperlinks, margins, one or two columns, and actual table sections. DOCX uses editable paragraphs, Word columns and tables. TeX uses fontspec, multicols and tabularx; TeXZIP contains the same source as `main.tex` with stable ZIP metadata. PDF uses ReportLab text, frames, tables and embedded exact fonts. No images or model calls occur. Repeated builds with identical inputs, dependencies and font bytes produce identical native bytes; the manifest records dependency versions and generator hash.

PDF generation requires the exact supplied font and its source glyphs. If unavailable, the requested PDF is **not** created; an editable TeX source is retained and the manifest records `native_source_pending`. The CLI exits 2 for any pending native source. DOCX and TeX sources declare the requested font even when it is unavailable locally, with that dependency explicitly pending. No fallback font or generic single-column layout is substituted.

This command never invokes a document renderer or compiler. It reports whether XeLaTeX is discoverable, but even an available compiler remains `available_not_executed`. All outputs retain `rendered_proof: pending`, `visual_review: pending` and `quality_status: not_evaluated`. Inspect every generated native file through the approved document runtime, including fonts, long-page flow, columns, tables, hyperlinks and complete text extraction. Bind the actual renderer and visual-review receipts separately using the existing evidence-bundle contract. A file signature or matching content hash is not a layout review.

`materialization-manifest.json` records the frozen packet/corpus digests, each original case and resume-content digest, requested recipe, native artifact path/hash, every auxiliary file hash, and missing dependencies. Existing destinations are refused. The tool emits no reviewer identity, reference approval, model output, render pass or qualification score.

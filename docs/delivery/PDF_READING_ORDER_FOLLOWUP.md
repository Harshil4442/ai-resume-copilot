# PDF reading-order follow-up for Stage 3/4

This is a local deterministic parsing candidate, not a qualified worker release.
It addresses the demonstrated case where independently authored left and right
résumé sections were merged into one line because their words shared a Y position.
No model request, source-document rewrite, corpus replacement or quality-flag
change is part of this change.

## Bounded extraction behavior

`backend/app/services/parsing.py` retains the existing word/font extraction and
rounded-Y line grouping for ordinary single-column documents, right-aligned date
rows and shared ruled tables. Within each line, original word coordinates put
mixed-font labels and their body text in left-to-right order, even when the PDF
painted those runs separately.

The additional path applies only when the page has two separately styled,
recognized section headings on one visual row. It uses pdfplumber's original word
coordinates, table cells and edges to verify a clear gutter for all subsequent
content on that page. Text before the paired headings remains a shared prelude;
the left section group is read before the right section group. Every extracted
word belongs to exactly one group. Source paint order is not the authority.

A ruled table with at least two rows and two columns containing both heading
cells retains row reading. Borders local to an independent column may extend
beyond its text without being cut through. A recognized independent-column page
with overlapping or spanning body text, a spanning horizontal rule, more than two
heading columns, insufficient supporting lines, or unavailable table geometry is
explicitly refused with `PDFReadingOrderError`. That error is rethrown before the
existing plaintext fallback, so a recognized ambiguity cannot silently become
interleaved facts. The original file or an editable/custom source remains the
alternative.

The native processor maps this one typed parsing error to the existing permanent
`document_inspection_refused` response. It exposes neither the source text nor the
parser's exception detail. Unexpected parser failures still return the existing
temporary unavailable response.

This is not a general document-layout classifier. Unstyled or nonaligned column
headings, nested columns, mixed horizontal bands, arbitrary tables and other
unknown layouts remain unqualified. The change does not remove discretionary
hyphens, repair existing extracted spacing, infer missing facts or promise
character-contiguous extraction for every document.

## Library basis and trade-off

The implementation uses the already pinned pdfplumber 0.11.10 and pdfminer-six
20260107; no dependency is added. The installed primary implementations inspected
were `Page.extract_text_lines`, `Page.find_tables`, `WordExtractor.iter_extract_tuples`,
`WordMap.to_textmap`, `cluster_objects`, `LAParams` and `LTLayoutContainer.analyze`.

`use_text_flow=True` preserves authored character/word flow instead of geometrically
sorting it. That is useful for some PDFs, but late-painted dates and row-painted
columns make it unsuitable as a universal replacement. Likewise, pdfminer's
`boxes_flow` settings still separated headings from their section bodies in the
authorized development example. The conditional geometry guard therefore keeps
the existing path unless the specific independent-section shape can be verified.
Conservative refusal trades coverage of ambiguous layouts for factual integrity.

## Local evidence

`backend/tests/test_pdf_reading_order.py` contains independently authored in-memory
PDF fixtures for left-first, right-first and row-painted columns; late-painted
dates; shared ruled table rows; spanning-body refusal; multiple pages; column-local
table borders; separately painted bold labels; three-column refusal; and a failed
geometry probe. They verify section ownership, dates, skills, contact facts,
source-byte identity and the retained source hyperlink. They do not substitute
expected text into the parser or serve as qualification-corpus replacements.

The focused local command completed with **72 passed**:

```sh
backend/.venv/bin/pytest -q \
  backend/tests/test_pdf_reading_order.py \
  backend/tests/test_resume_layout.py \
  backend/tests/test_pdf_streams.py \
  backend/tests/test_pdf_stream_isolation.py \
  backend/tests/test_docx_parsing.py \
  backend/tests/test_document_processor.py::test_recognized_pdf_reading_order_refusal_is_permanent_and_private \
  backend/tests/test_document_processor.py::test_unexpected_parser_failure_is_unavailable_not_permanent
```

The additional processor-boundary regression verifies that a recognized ambiguity
is permanently refused without exposing exception detail; the existing unexpected
parser-failure regressions preserve temporary-unavailable behavior. All scanner
responses in those transport tests are synthetic, not native scanner evidence.

Running the whole `test_document_processor.py` with those parser suites produced
218 passed and two failures in
`test_only_explicit_exec_mode_consumes_controlled_nonzero_supervisor_result`
(`code=1/2`, `allow_outcome=True`). The same failures reproduced from the exact
unchanged base `6842661` test source against unchanged `service.py`; they exercise
the supervisor, not this parser change. The broad run is therefore not reported
as all green. The temporary baseline-copy test file was removed after the check.
Ruff on both changed runtime modules and test files, and Mypy on `parsing.py`,
passed locally.

The existing tests retain native PDF editing/stream-isolation, rotated text,
single-column layout and DOCX parsing checks. No native edit-unit, font, byte or
width bound was relaxed.

Read-only post-implementation checks on the explicitly authorized development
cases 123, 133 and 163 kept all original extracted word tokens and separated the
summary, experience, projects, education and skills sections. Original PDF hashes
were unchanged. These observations are development diagnostics only. No held-out
case was inspected or used to tune this change, and the frozen quality flags were
left unchanged.

## Before deployment or qualification

`backend/scripts/document_test_image.py` lists `parsing.py` in the native document
runtime sources. This commit therefore requires a new immutable document-worker
image and exact runtime/source identity, followed by the established native
rendering, scanner, format, reading-order and bounded-edit acceptance checks.
The current worker image is not qualified for this source change by these local
tests. Existing human quality review and other release gates remain applicable.
This follow-up does not change or join the currently running release candidate.

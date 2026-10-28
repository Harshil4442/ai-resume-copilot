# Supported-format synthetic quality packet v2

This is a **new input packet**, derived from the frozen v1 synthetic packet. It
keeps all 200 case IDs, resume/job content, risk cohorts, and the 40/160
development/held-out split. It does not replace or edit v1. The manifest binds
the v1 manifest and corpus hashes, the new case hashes, the protocol, and the
rubric. Rebuild only into a different versioned directory:

```sh
PYTHONPATH=backend python backend/scripts/build_supported_quality_packet.py \
  --source backend/evals/unified_credit_v1 \
  --destination /path/to/a/new-version
```

The source recipes change only where the deployed native policy could not use
the v1 fixture:

| Source | v1 font/compiler | v2 font/compiler |
| --- | --- | --- |
| PDF | Exact embedded Times New Roman or Courier Prime | Unchanged |
| DOCX | Times New Roman or Courier Prime | Installed Liberation Serif or Liberation Mono |
| TeX, TeXZIP | `fontspec`, XeLaTeX | Latin Modern Roman or Mono, `pdflatex -no-shell-escape` |

The DOCX and TeX changes are **fixture changes**, not proof that HireWiz can
restyle a user's original document. The original v1 fonts are not silently
substituted. The existing `risk:unusual_fonts` cohort remains a distinct
monospace source style in v2; the human rubric should judge the new style.

Materialize these sources into private evidence storage with the offline
materializer and exact local font files. The materialization manifest stays
`not_evaluated`; it does not claim a native render, visual review, model score,
or release authority. The PDF font files must be supplied separately; do not
commit a licensed Times New Roman font. A DOCX font must resolve to the exact
Liberation family in the native document image for a valid worker receipt.

Before any model comparison or retail route approval, obtain native-worker
receipts for each source, extracted-text and rendered-page checks, visual
review, actual funded model outputs, and two independent blinded human scores
with adjudication. Do not use the held-out 160 cases to tune a route. The
packet's 200 supported source recipes are a prerequisite, not a completed
quality evaluation.

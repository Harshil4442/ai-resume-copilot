# Companion 0.8.5 prepared source release

This follow-on starts from main `97a7492f320966f1353dbdcefececc3b4a3747af`.
It integrates the independently reviewed startup diagnostic `813173de`, trusted
key-phase diagnostic `0be04f3` and quarantined local recovery `43008e2`.
The application release candidate, backend, credentials and cloud are unchanged.

The release adds startup support codes and one explicit local recovery for a
genuinely missing retained signing key. Original key/checkpoint/evidence stores
remain unchanged and quarantined. A fresh key uses separate stores and needs
fresh connection/fingerprint approval. Unknown application outcomes are never
replayed; old-device links open the existing review flow and do not establish
revocation. Pending/drift outcomes stay locked without automatic retry or another
recovery. See [the recovery boundaries](../../browser-companion/LOST-KEY-RECOVERY.md).

The manifest, npm metadata and package guards identify 0.8.5. The exact source
inventory has 20 files; permissions, extension identity, native protocol 2,
executor `companion_0_8_1`, KMS pins and signed action deadlines are unchanged.
The reproducible new ZIP is `hirewiz-browser-companion-0.8.5.zip`, SHA-256
`039f0236d3f391b45e602c52de7970adbb1c8930511b813b49e6c3302f99555a`,
75,391 bytes. All eleven earlier archives remain unchanged. The existing website
setup link and its focused test point to this new archive; no other frontend flow
is changed.

Local checks passed: 242 deterministic companion cases (including three package
integrity/reproducibility controls), 12 owned native Chrome cases, and 162 focused
website setup/review cases. A separate source/ZIP comparison checks all 20 exact
entries; rebuilds reproduce the same digest. Negative package cases refuse source
drift, unknown source files and inconsistent release metadata without rewriting a
delivered archive. Follow-on CI, publication and an owner journey are not claimed.

Local verification and source review do not prove publication, an installed
owner 0.8.5 session or a completed actual recovery. The earlier 0.8.4 diagnostic
installation was a private QA override, distinct from its official public ZIP.
Human confirmation of a new owner signing key remains a handoff; a separate
Start, fresh fingerprints and existing website/password approvals are required.
No employer application or provider receipt is used as a smoke test.

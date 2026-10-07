# ADR-006: Data and Artifact Ownership

- Status: Accepted
- Date: 2026-08-03
- Updated: 2026-10-07

## Context

Career records, billable state, generated output, and uploaded files have different durability and access requirements. Redis or a provider response cannot be the sole copy of user-owned or accounting state.

## Decision

PostgreSQL is authoritative for accounts, opportunities, immutable job snapshots, approved evidence, resume-version metadata, run state, entitlements, usage, outcomes, and audit records. Redis is optional cache and coordination only.

The parser retains each validated original PDF or DOCX in a nullable, deferred PostgreSQL binary column. Upload size limits bound individual files; list and metadata queries do not load the binary. This keeps the source and its owner in one deletion transaction and supports source-preserving tailoring without a separate storage dependency. Source and version-download endpoints require ownership and return private, non-cacheable responses. Account JSON exports contain source metadata, while original files are downloaded through the authenticated source endpoint.

Tailored versions store stable source-location replacements and evidence IDs, not duplicate binaries. Exports apply those replacements to the immutable original and reject unsafe formatting changes. Historical uploads have no retained source and must be uploaded again; the original cannot be reconstructed from extracted text.

Analysis input and result payloads follow configured retention windows. User-owned workspace records remain until user deletion. Payment audit records may be retained after removing the live customer link.

## Alternatives

- Store durable state in Redis: rejected because eviction or cache loss cannot affect billable or user-owned records.
- Private Google Cloud Storage with signed URLs: deferred for the current bounded resume workload. Adopt it if file volume, database backup cost, or retrieval load warrants separating binary storage; retain owner checks and transactional deletion bookkeeping.
- Add a dedicated vector database now: rejected until a measured retrieval use case requires it.

## Consequences

Every durable record has an ownership boundary and deletion strategy. Original-file retention increases database storage and backup size, so volume should be monitored. Moving files into object storage requires private-bucket IAM, signed URL tests, lifecycle rules, and account-deletion integration. File bytes never appear in ordinary metadata responses or model-call telemetry; tailoring sends only the selected editable text and approved evidence to the provider.

## Reversal Conditions

Reconsider if regulatory isolation, larger upload limits, database storage/backup cost, or measured retrieval scale requires a separate storage boundary.

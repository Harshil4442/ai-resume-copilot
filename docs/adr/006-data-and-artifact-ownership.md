# ADR-006: Data and Artifact Ownership

- Status: Accepted
- Date: 2026-08-03

## Context

Career records, billable state, generated output, and uploaded files have different durability and access requirements. Redis or a provider response cannot be the sole copy of user-owned or accounting state.

## Decision

PostgreSQL is authoritative for accounts, opportunities, immutable job snapshots, approved evidence, resume-version metadata, run state, entitlements, usage, outcomes, and audit records. Redis is optional cache and coordination only.

The current resume parser discards uploaded PDF or DOCX bytes after extracting the private structured working copy, so it does not retain an original binary artifact. When rendered resumes or retained original files are introduced, store them in a private Google Cloud Storage bucket and keep only an artifact reference in PostgreSQL. Access must use short-lived signed URLs after ownership validation.

Analysis input and result payloads follow configured retention windows. User-owned workspace records remain until user deletion. Payment audit records may be retained after removing the live customer link.

## Alternatives

- Store durable state in Redis: rejected because eviction or cache loss cannot affect billable or user-owned records.
- Store all binaries in PostgreSQL: rejected because large immutable files do not fit the transactional workload.
- Add a dedicated vector database now: rejected until a measured retrieval use case requires it.

## Consequences

Every durable record has an ownership boundary and deletion strategy. Object storage remains absent while no binary is retained, avoiding unused infrastructure. Adding artifact retention later requires private-bucket IAM, signed URL tests, malware policy, lifecycle rules, and account-deletion integration.

## Reversal Conditions

Reconsider only if regulatory isolation, very large structured artifacts, or measured retrieval scale requires a separate storage boundary.

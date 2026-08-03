# ADR-005: Approved Evidence Boundary

- Status: Accepted
- Date: 2026-08-03

## Decision

Only user-approved evidence may support generated candidate claims. Generated resume
lines retain evidence IDs and source snapshots. Interview coaching either cites approved
evidence or reports that evidence is needed.

## Why

Trust and factuality are durable product advantages. Plausible but invented metrics or
experience create material user risk and make outputs impossible to audit.

## Consequences

Tailoring is unavailable until evidence is approved. Unsupported requirements are shown
as gaps, not woven into candidate history. Prompt changes are versioned and model calls
record operational metadata without logging resume text.

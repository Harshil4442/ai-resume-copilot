# ADR-004: PostgreSQL Usage Ledger

- Status: Accepted
- Date: 2026-08-03

## Decision

Use append-only `usage_events` plus a compatibility balance on `users.ai_credits`.
Operations reserve before provider work and commit only on success; failures release.

## Why

A balance alone cannot explain a charge or recover safely from provider and worker
failure. PostgreSQL transactions and idempotency keys provide an auditable source of
truth.

## Consequences

Premium work records a zero-unit waiver. Manual adjustments, when added, must record an
actor and reason. Redis is never authoritative for usage.

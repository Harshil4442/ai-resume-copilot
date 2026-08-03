# ADR-003: Durable Analysis with Cloud Tasks

- Status: Accepted
- Date: 2026-08-03

## Decision

Represent expensive work as an `analysis_runs` state machine and dispatch production
work through Google Cloud Tasks to a private Cloud Run worker.

## Why

Interactive requests should return quickly, refreshes must not lose work, duplicate task
delivery must be harmless, and provider retries need durable attempt state.

## Consequences

Workers claim a queued or stale run atomically. A duplicate delivery returns the existing
terminal state. Retryable failures receive at most three attempts; terminal failures
release reserved usage. Local development may use inline or background execution.

# ADR-002: Modular Monolith

- Status: Accepted
- Date: 2026-08-03

## Decision

Keep one backend codebase and database while enforcing billing, career, analysis, and
usage domain modules. Do not introduce user-facing microservices at the current scale.

## Why

The product benefits from transactional integrity and fast iteration more than network
isolation. The analysis worker is a separate runtime because its scaling and trust
boundary differ, but it reuses the same domain code and image.

## Consequences

Cross-domain writes use explicit service calls and database transactions. A future
service extraction requires measured scaling or ownership pressure, not fashion.

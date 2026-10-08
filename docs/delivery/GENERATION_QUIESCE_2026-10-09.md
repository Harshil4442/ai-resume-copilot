# Schema-compatible generation pause

Status: verified local source on schema `20261008_0009`; no cloud rollout yet.
This compatibility release is based on `e7d8f2b` and lets deterministic services continue
while optional AI generation is paused before the monetary schema transition.

Set `OPTIONAL_AI_GENERATION_ENABLED=false` explicitly on both API and analysis worker.
The provider gate runs before constructing a model client or admitting an attempt and
again before each fallback. New enhanced admissions stop before product holds. Worker
analysis and dispatch gates run before claims, preserving queued work, reservations and
attempt counters. Exact and semantic immutable request replays and terminal duplicates
remain idempotent. Basic matching, curated interview practice, direct answers, default
parsing, requested-enrichment parsing fallback, employer discovery and billing remain
available. The unset flag preserves the earlier release's behavior; it is not a pause.

Root verification uses the frozen normal dependency lock and owned disposable local
PostgreSQL/Redis services: **957 backend tests pass without skips**, with three existing
dependency deprecation warnings. The 27 new focused cases verify the actual admissions,
worker claims, deterministic operations and gate-before-fallback boundary. Targeted Ruff,
Mypy and whitespace checks pass. No model, candidate application or payment request was
performed against an external provider. Remote immutable CI and cloud checks are separate.

## Required deployment sequence

1. Serialize release writers and inventory current API/analysis revisions, tags, queues
   and maintenance targets. Preserve private employer workers, discovery and billing.
2. Promote the tested compatibility image with direct ASGI startup, automatic migration
   disabled and generation explicitly paused. Pause the analysis queue and remove old
   revision tags. Keep maintenance directed to current workers.
3. Verify retirement of all earlier generation-capable writers before migration.
   Zero traffic, a 900-second request timeout, elapsed waiting or quiet logs alone do
   not prove process termination. If retirement remains unresolved, retain the gated
   deterministic service and stop before advancing the schema or enabling generation.
4. Apply the backed-up additive monetary migration, then stage the cost-enforced image
   with generation still paused. Verify schema, immutable image/revision identities,
   deterministic operations, health and private worker access before promotion.
5. Configure the exact reviewed model and finite pricing policy. Enable generation and
   resume analysis only after the complete writer/policy verification succeeds.

The flag applies only to code containing this gate; it cannot change an older immutable
revision or revoke an already-created client. Removing a key from a new revision does
not erase credentials in old instances. Any revision retirement or credential fence must
be separately justified from current runtime evidence. Rollback uses the gated image
with the advanced additive schema retained; it never restores old unbounded AI writers.
Unknown provider outcomes and monetary holds are preserved during recovery.

Google documents that request timeout does not terminate the instance and that traffic
changes preserve in-flight work. These constraints motivate the explicit retirement gate:
[request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout),
[traffic migration](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration).

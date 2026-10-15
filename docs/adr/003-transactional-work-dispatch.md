# ADR 003 Transactional work dispatch

Status: Accepted for implementation on 8 October 2026

Store queue intent alongside the domain change and credit reservation in PostgreSQL.
Publish committed intent to private Cloud Run workers through Cloud Tasks. A scheduled
sweep recovers an API crash before publication or a queue failure; pending work remains
visible instead of disappearing between a database commit and a network request.

Task names are deterministic for a dispatch record and execution generation. Publisher
and executor claims use short compare-and-set leases and fencing tokens. Do not hold SQL
transactions across model calls, crawling, rendering or employer network requests. Duplicate
delivery is expected. Domain state and financial uniqueness constraints are authoritative.

Application handlers persist the final submitting transition before sending. A possible
external send is never retried blindly; recovery changes it to unknown and reconciles the
provider receipt. A queue acknowledgement is not employer confirmation. External exactly
once delivery cannot be guaranteed by a SQL transaction or Cloud Tasks.

Use separate ingestion, search and application queues with independent dispatch budgets.
Keep the existing analysis worker and GCP region; add dedicated private workers when their
workload requires independent CPU, connection or secret isolation. Bound database pools
against maximum instances across all workers. Expose no internal worker endpoint publicly.

Trade-off: an outbox adds SQL writes, a periodic publisher and observable pending states.
It provides durable recovery without introducing a second financial authority or replacing
the existing GCP architecture. The failed dispatch table retains exhausted work for support.

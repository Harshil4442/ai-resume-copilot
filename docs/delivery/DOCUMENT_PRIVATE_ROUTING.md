# Private document worker routing

The document worker retains internal ingress and Cloud Run IAM. The API and
analysis worker call its canonical `run.app` origin; they need a VPC route for
Cloud Run to recognize those requests as internal. This dependency does not
establish that the overall deployment outcome is complete.

The fixed proposal in `infra/gcp/document_network.py` uses project
`ai-resume-parser-482412`, a custom-mode network `hirewiz-private`, and the
`us-central1` subnet `hirewiz-callers-us-central1` (`10.42.0.0/24`). Check actual
inventory and address conflicts before provisioning. Private Google Access is
enabled on that subnet. A private Cloud DNS zone `hirewiz-private-run-app`
resolves `run.app.` to `199.36.153.8`, `.9`, `.10`, and `.11`, with
`*.run.app.` as a CNAME to the apex. Both records use TTL 300.

Only the callers use Direct VPC egress with `private-ranges-only`. The worker
does not receive a VPC attachment. The caller setting includes Private Google
Access addresses, while public Neon, Redis and third-party destinations retain
direct internet routing. No Serverless VPC Access connector, Cloud NAT or load
balancer is required for this proposal. Cloud DNS still has zone/query charges.

Deployment requires native confirmation of enabled Compute/DNS APIs, subnet
capacity, the default internet gateway route, applicable firewall and
organization policies, and service-agent permissions. Caller runtime identities
need `roles/run.invoker` on the document worker and Google-signed ID tokens whose
audience is its canonical origin. Do not introduce service-account keys.

`verify_caller` compares v1 revision annotations or v2 template network settings.
`verify_network` compares native Compute and Cloud DNS metadata. These checks
make no provider calls and cannot prove live HTTP connectivity, identity,
firewall behavior or document acceptance. Bounded metadata lists must include
one excess row before refusing truncation: DNS limit 5 (accepted maximum 4,
including normal NS/SOA records), routes limit 65 (accepted maximum 64).

Before promotion, execute a Cloud Run diagnostic in the same VPC with a real
caller runtime identity. Verify worker DNS resolves to the private VIPs, invoke
synthetic `/inspect` and `/validate-docx` cases, and verify external HTTPS plus
the staged database health path. Fetch ID tokens in memory and retain only
sanitized outcomes and execution identity. Direct VPC startup can delay
connectivity by a minute or more; use bounded retries. Metadata agreement alone
must not pass this acceptance step. No native resource creation or acceptance
is asserted by this document.

Primary references: [Cloud Run private networking](https://docs.cloud.google.com/run/docs/securing/private-networking),
[Direct VPC configuration and permissions](https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc),
[exact egress flag semantics](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy#--vpc-egress),
[Private Google Access DNS and routes](https://docs.cloud.google.com/vpc/docs/configure-private-google-access),
[service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service),
[Cloud DNS pricing](https://cloud.google.com/dns/pricing).

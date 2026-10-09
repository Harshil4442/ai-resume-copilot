# Private ingress and browser compatibility integration

Status: bounded local transport safety and website composition verified. Contention
availability, protected browser connection, new exact-source CI and production remain
open. Production traffic and settings have not changed.

## Integrated result

The exact independently reviewed 28-path ingress result is integrated on `0993b23`.
It authenticates the website's server transport separately from candidate sessions,
binds the canonical action/body/candidate commitment, and refuses uncertain native
publication. Root verified the frozen source and both artifact manifests before
applying the reviewed preimages. The default production factory stays unavailable.

| Local boundary | Verification |
| --- | --- |
| Private transport and lifecycle | 171 backend cases pass without skips, using actual disposable PostgreSQL and native emulator |
| Website projection | 206 frontend cases, lint, types and production build pass |
| Genuine candidate browser journey | 46 checks pass through Chromium, HTTPS NextAuth/BFF, FastAPI, PostgreSQL and native emulator |
| Public signup rate limit | Seven actual BFF checks: five enrollments return 200; the sixth returns fixed `RATE_LIMITED` HTTP 429 without creating lifecycle state |
| Public 429 regression | Six intended assertions failed before the root repair and pass afterward; the original upstream 500 and BFF 503 observations remain retained |

The root public projection repair uses a fixed safe rate-limit status/message. An
unknown outcome still requires checking the retained request; it does not trigger an
automatic new registration. It does not forward private upstream bodies or headers.
The 429 repair was authored by root and is not presented as a separate independent
clearance. The preceding ingress transport was independently reviewed.

The independent concurrent-process availability probes still produce two UNKNOWN
results when publication acknowledgement is ambiguous. Safety's at-most-one property
passes; exactly-one availability remains a HOLD. Ordered acknowledged publication
followed by independent replay is refused correctly. These narrower passing results
do not diagnose the preserved initial password-reset contention failure.

## Exact CI failures and compatibility repair

The exact `0993b23` [CI run](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37939583249)
passes all 2,030 backend cases, security/container and companion jobs. Its frontend job
has 185 passing and 20 failing responsive cases; its cold browser job also fails.
Both failures are preserved and have distinct reproduced causes.

The responsive fixtures still intercepted only the old generic proxy and missed
`/api/account/profile`. Three fixture files now intercept the reviewed dedicated
profile handler. All 224 original assertion-bearing lines remain identical. The four
targeted failures now pass, and all 205 original cases pass once each across widths
320, 390, 768, 1024 and 1440, without skips/retries. Product UI/authentication source
was not weakened to satisfy those fixtures.

The cold production test used HTTP while the website account transport requires
HTTPS. Its five-path repair provisions a disposable loopback certificate, keeps
strict server-to-server hostname/CA validation, and verifies the actual secure
session cookie and public signup projection. Fourteen runner checks pass. The
complete root-composed browser/API/SQL journey also passes without BFF interception:
original/custom files remain byte-exact, three requested jobs deliver two, six
credits reserve/four charge/two release, ownership and stale approval are enforced,
and manual handoff creates neither an automatic fee nor an employer receipt. Five
widths have zero recorded accessibility violations and page/server errors.

The native authority/signing dependencies in the candidate journey and payment/feed
transports in the legacy cold journey are explicit synthetic fixtures. Neither journey
is an employer submission or proof of production custody. Each owned browser run
reports its own server groups stopped, SQL schema removed and private TLS key removed.
Component test emulator namespaces intentionally retained for evidence are not
claimed globally cleaned.

## Remaining release checkpoints

- [x] Integrate the exact bounded independently reviewed private transport.
- [x] Verify fixed public HTTP 429 with actual BFF/API/native composition.
- [x] Reproduce both CI browser failures and integrate compatibility repairs.
- [x] Verify root-composed real HTTPS browser/API/SQL flow and full responsive suite.
- [ ] Pass all five new exact-source GitHub CI jobs.
- [ ] Resolve retained contention availability and genuine protected browser connection.
- [ ] Verify production resources, writer retirement, guarded migration and rollout.

The separate protected-join independent review has no final clearance after automatic
review rejection. Its proposed patch remains outside root. Exact frozen references,
bounded evidence and original failures are recorded with the existing component
reports and [responsive evidence](evidence/2026-10-09-responsive-ci-compatibility.json).

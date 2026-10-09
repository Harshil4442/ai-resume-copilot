# Password-change and MV3 component integration — 9 October 2026

The reviewed disabled password-change component and actual MV3 runtime are integrated
into source. The combined local backend run passes **1,987 tests**, with zero failures,
errors or skips; frontend **124 tests**, lint, types and production build pass. Companion
checks pass **76 unit, 6 actual Chromium runtime/socket and 14 existing Chromium safety
cases**. Production deployment, protected first connection and the full application
service remain incomplete.

## Exact component review and local changes

The seven-file password-change freeze was verified against its independent report:
74 reproduced and 16 independent cases pass. The fifteen-file MV3/UI freeze was verified
against its independent report: 103 reproduced and 12 independent cases pass. Both
patches were applied only after exact base/result and evidence hashes matched. The two
original native lifetime modules remain unchanged.

Root added one deterministic fault-injection test on the actual local native/protected
fixture. Exhausted definite pre-commit ABORT retries produce UNKNOWN with no original
acknowledgement, no credential projection and no new-generation eligibility. Old sessions
remain denied, the unrelated candidate remains usable and repeat execution is status
only. Every original reset test/function/class AST is unchanged. CI now checks this test
and runs the actual MV3 restart/failure suite.

## Preserved failures and limits

The first combined reset run passed 1,985 of 1,986 tests. Its unchanged two-request
concurrency test produced zero successful winners. Unchanged diagnostic isolation and
four controlled repetitions passed, but do not explain or repair that full-run failure.
The subsequent expanded full run passed with a privacy-safe diagnostic observer around
the actual original methods. It records only phase/type classifications, never request
bodies, passwords, hashes, tokens or session values. The initial failure remains an
unresolved availability concern; no retry limit, assertion or production guard was weakened.
The next exact remote CI must verify ordinary uninstrumented execution.

The first local frontend build correctly refused a missing production NextAuth signing
secret. A process-only synthetic build secret corrected the local environment; the final
production build passed. This creates no production secret or configuration.

Independent review of the separate website lifecycle patch found real generic-transport
bypasses: login exposed bearer/private native context, and browser-selected logout UUIDs
could strand private cookie cleanup. Its original patch is **not integrated**. The author
is repairing browser routing and separately adding trusted private backend ingress; both
need review. Passing original cold-browser checks does not clear these findings.

The protected challenge/signing/dispatch/activation join is separate active development.
Neither this MV3 checkpoint nor its helper syntax check proves a genuine composed first
connection. Device identity alone grants no application action, upload or submission.

## CI and production state

The previous runtime integration has all five successful exact jobs at
[`46274a5`](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37924962950),
including 1,953 backend tests and repository history scanning. New source CI remains
pending. The previous history scan's source-digest false positives and narrow correction
are preserved separately.

Read-only production checks still observe backend `e7d8f2b` on API revision
`ai-resume-parser-00267-xot` at 100% traffic and public health HTTP 200. The later
generation-quiesce revision remains at zero traffic. The serving schema remains the
previously verified `0009`; this source contains later migrations and cannot be promoted
through the legacy release path. Vercel remains the previously promoted `06cc112` release.
No production authority, credential rotation, migration, employer application, model call
or payment was performed by this integration.

Machine-readable hashes, exact local results and open gates are in
[the evidence record](evidence/2026-10-09-reset-mv3-component-integration.json).

# Writer version check during V3 native proof

## Observed outcome

The definitions-only V3 image built on 2026-10-28 passed its one-time inventory Job: its identity preflight returned in 2,102 ms, and the renderer and font manifest matched the pinned profile. Its separate one-time 11-case Job failed **before any case started**. The same identity preflight returned code 1 after 5,319 ms; its sandbox cleanup returned code 0. The failed Job is recorded for read-only reconciliation and must not be executed again as a retry.

`docx_renderer._version()` caps its owned LibreOffice `--version` process at five seconds even when the surrounding identity preflight has a 45-second deadline. The failed duration is consistent with a cold-start timeout. The current receipt does not identify which preflight statement failed, so this is a likely cause, **not a proven diagnosis**. An immutable image and an earlier successful inventory also cannot establish that the failed corpus passed.

## Safe release options

1. For immediate scanner restoration, build a new definitions-only successor from the unchanged, qualified V3 parent with a new nonce and prove its inventory and 11-case Jobs once each. Preserve this failed Job and receipt as failure evidence. Never replay its execution or treat an inventory result as corpus acceptance.
2. If the version check continues to fail, use a separate, one-time synthetic diagnostic Job to report only which bounded preflight phase failed. Keep the same sandbox launcher, fixed image, cleanup, network isolation and no candidate data. Diagnostic output is not release acceptance.
3. For a durable longer version budget, change `infra/document-processing/docx_renderer.py::_version` from the five-second cap to a reviewed bounded cap such as 15 seconds, still limited by the caller's absolute deadline. Update `docx_layout.owned_writer_version_seconds` in `infra/document-processing/policy.json`, its exact runtime source hash, the probe's expected runtime hash, and all source/build/profile attestations. This changes the policy and runtime bytes; it requires a **new full-source image**, a new immutable image digest and policy hash, and new native inventory, 11-case, malware, layout and refusal proof. Update caller and worker policy pins only through the reviewed transition. A definitions-only child of the old V3 base cannot contain this change.

## Verification for the durable change

- Unit test that a slow synthetic version process can finish within the new cap, while a process past the cap fails closed and is reaped by the owned launcher.
- Test that a shorter caller deadline still wins, and that `--version` keeps one attempt and its fixed executable, private profile, output limit and exact version check.
- Recompute source and policy hashes, then run the existing source-contract and native 11-case gates against a newly built image. Do not accept a local mock as native proof.

This note changes no runtime code, policy, deployment or billing flag.

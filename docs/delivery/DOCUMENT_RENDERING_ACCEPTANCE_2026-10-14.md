# Document rendering acceptance — 14 October 2026

The corrected document image passes all six original acceptance cases in GCP.
This closes the demonstrated DOCX rendering defect; it does not complete the
resume product workflow or authorize production activation.

| Original case | Observed result |
|---|---|
| Clean PDF | Parsed successfully; exact source binding and antivirus metadata verified |
| Clean DOCX | Parsed successfully; exact source binding and antivirus metadata verified |
| EICAR input | Worker refused the synthetic antivirus fixture |
| Direct EICAR detection | Coherent detection of the unchanged exact fixture |
| Styled multipage edit | Accepted; two pages, one link and two graphics preserved; protected geometry displacement and pixels changed outside the edit region both measured zero |
| Unsafe rendered reflow | Refused with `hyperlink_changed`; no accepted artifact was produced |

Every case was attempted and completed its sandbox cleanup. The suite took
131.310 seconds. Cloud Run reports one succeeded task, zero failed tasks and
zero retries. The temporary Job and Execution were deleted using their original
identities, then independently verified absent. The exact Build source generation
was separately deleted and verified absent.

The repair uses a separate owner process for each Writer invocation. It retains
child ownership until bounded cleanup and reaping finish, including adopted
descendants. One genuine engine exit 81 may request a restart within the same
original deadline. A failed or unconfirmed cleanup refuses the render. Scanner,
layout, resource and isolation requirements remain intact.

The source archive includes twelve regular files; eleven installed runtime
files were checked against their exact hashes before the cases ran. The
read-only font inventory was derived from the new image and verified again
inside the native run. No previous image's font receipt was used as acceptance.

| Binding | Verified value |
|---|---|
| Cloud Build | `bdf4808e-3bce-4b28-92a5-7393c3657bab`, SUCCESS |
| Immutable document image digest | `sha256:624597726ce2fefa2b3574fbf0a8ec4ccdc02a826380d76c5edc8ebbd59aa107` |
| Source archive SHA256 | `84326bb740f847b63d49e18676a3c14c57063c6b4232b68bf7e555451eaeae31` |
| Policy SHA256 | `dbd15ce3120bb6211bb6c1d7b166aaddbd8b80e00a72d564e6274ef10db3aaf1` |
| Font inventory SHA256 | `abaf429caeeeb5fbcc000c878a315c89e23ecd3dc912bb74df10761c144f7d60` |
| Renderer version | `LibreOffice 25.2.3.2 520(Build:2)` |
| Sanitized native observation SHA256 | `97431d7e88d7b7d81824f33178d9c117f052894663d3ec0d40e62bbeea1f8ddc` |

The Build used sealed, explicitly reviewed working bytes. Its historical source
label does not prove that this archive equals a clean Git commit. The original
Build-create acknowledgement was unavailable; a matching native Build was
recovered through bounded read-only observation and its full source/image joins
were verified. No Build-create retry was made.

The policy and disabled worker declarations agree in source. API wiring remains
disabled. When preparing a reviewed deployment, the API must bind the immutable
image, policy, renderer version and font inventory above to the actual worker
origin. Font/version variables belong on the API; the worker retains its fixed
image/policy environment contract.

Remaining acceptance includes truthful edits, original/custom/tailored resume
selection, review and downloads, genuine staged API integration, and production
release. Arbitrary DOCX or Microsoft Word layout equivalence has not been proved.
The cause of the earlier wrapper exit 137 remains unconfirmed. The approved
public diagnostic is [Cloud Run issue 573358022](https://issuetracker.google.com/issues/573358022).

# HireWiz reviewer instructions

Prepared 28 October 2026. This guide is ready to share with reviewers. The first exploratory interview package now contains 10 synthetic cases, 20 actual A/B outputs and two blank scoring sheets. These outputs are not an approved reference or a release qualification; keep them separate from any later corrected batch.

## Purpose

Help us check whether HireWiz can lower its costs while keeping the quality of its résumé and interview services. Your job is to judge the results against the candidate’s supplied information and the job description. You are not deciding whether the candidate should be hired.

## People needed

- **Reviewer 1 and Reviewer 2:** two different people with résumé, recruiting or relevant professional experience. Both review each output independently.
- **Adjudicator:** a third person who resolves disagreements using the source information and the reviewers’ written reasons.
- **Coordinator:** prepares the files, keeps the hidden A/B identities and collects the sheets. Someone who knows those identities should not score the outputs.

Use your actual name or an assigned private reviewer ID. Do not use an AI system to fill in the scores. We have not assigned people or hired anyone through this guide.

## What you will receive

For each case: the original résumé, the job description, a list of facts approved for use, the exact service requested, and two outputs labelled **A** and **B**. For résumé tailoring, you also receive the original and changed document files and page previews.

You will not receive the model names, service prices, which output is cheaper, or the other reviewer’s scores. A/B are anonymous labels; do not assume A is the old version or B is the new one.

If a source, page or output is missing, unreadable or reveals the hidden identity, stop that case and tell the coordinator. Leave its scores blank and explain why. Missing material is not an acceptable result.

## Review one case

1. Read the original résumé, job and approved facts before reading the outputs.
2. Review A on its own. Check the facts and the specific service requirements below.
3. Give A four scores and a concrete reason. Record any critical problem separately.
4. Repeat for B using exactly the same standard.
5. Save your own sheet. Do not compare scores with the other reviewer before submitting it.

Do not edit an output to repair it. Record what needs fixing. Do not copy actual customer information into a public AI tool or public document.

## What to check

| Service | Required checks |
|---|---|
| Tailored résumé | Changes are useful for this job and supported by approved facts. No invented skills, dates, achievements, responsibilities or numbers. The original format is preserved: section order, fonts, spacing, columns, tables, links and all unaffected content. Inspect every page for clipping, overlap, blank pages and missing text. |
| Interview questions | Exactly **eight distinct questions** relevant to the role, with useful preparation guidance. Missing personal evidence is acknowledged. The system does not invent first-person answers or achievements. |
| Enhanced job match | Strengths and gaps agree with the résumé and job. “No evidence supplied” is distinguished from “the person cannot do this.” A match score is not presented as a hiring probability. |
| Résumé enrichment or bullet rewrite | Extracted facts and rewritten claims remain supported. Stronger wording must not exaggerate ownership, scale or results. Names, dates, tools and metrics remain accurate. |
| Questions and answers | The answer addresses the question, uses the supplied evidence and makes uncertainty clear. A citation must actually support the associated claim. |
| Learning plan | Steps address the relevant gaps and are concrete enough to follow. Suggested projects are future work, never represented as completed experience. Resource links must support the advice. |

For tailoring, an unchanged copy is not a successfully tailored résumé. A different template is not successful preservation. The coordinator will verify that the reviewed and downloadable files are identical; report any discrepancy you notice.

## Score each output from 1 to 5

| Dimension | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Relevance | Wrong goal | Mostly generic | Relevant, with material omissions | Specific to the role and evidence | Precise and prioritizes the most relevant requirements |
| Completeness | Missing or unusable | Several required parts missing | Core result present, but a material part missing | All required parts present | Complete and well integrated without unnecessary content |
| Clarity | Unclear or contradictory | Difficult to follow | Understandable, with some ambiguity | Clear and organized | Precise and easy to act on |
| Usefulness | Cannot support the task | Needs major correction | Useful after material revision | Useful with minor review | Directly useful after normal candidate review |

Give a specific reason, such as: “Question 4 is relevant, but its answer guidance invents team-management experience.” Avoid reasons such as “good,” “looks professional,” or “I prefer A.” Attractive wording cannot compensate for an inaccurate claim.

## Critical problems

Enter `true` or `false` in each critical-problem column:

- **factual_error:** an incorrect or unsupported factual claim, including an invented metric or achievement.
- **privacy_error:** another person’s information, confidential content or other information that should not be in this output.
- **document_corruption:** lost content, broken reading order, clipping, overlap, a damaged file or an unwanted layout change.

Explain the exact location and problem in `notes`. Any critical problem holds the affected route for investigation; it must not be hidden by averaging high scores. If you cannot check a field, leave it blank and explain the missing evidence instead of entering `false`.

## Unsupported files and disagreements

A clear refusal of an unsupported file is a separate outcome. Do not score it as a successful résumé or silently remove it from the case list. The coordinator records whether the refusal was correct and whether any charge was restored.

The adjudicator reviews every disagreement against the original material and both explanations, then records a resolved score and reason. They must not choose the score that makes a release pass. Conflicting critical-problem flags also require investigation before release.

## First review batch and return instructions

We will start with **10 development cases that pass native PDF preflight**, for interview questions first. That means 20 outputs per reviewer, 40 independent review rows in total. The proposed cases are fixed in advance; a failed case remains visible in the report. This small batch helps find problems and is not a final quality certification.

The coordinator supplies the actual outputs and pair IDs with each versioned package. Each reviewer fills only their own CSV and returns it privately to the coordinator, identifying any incomplete cases. Keep the supplied column names and A/B labels. Do not exchange sheets with the other reviewer or mix scores from different output versions.

Later, we will use a separate held-out set that was not used to improve the system. A qualified analyst checks the planned statistical comparison. Human scores alone, or this first batch, do not automatically authorize paid launch.

## Work available now

Reviewers can examine the synthetic source résumés, job descriptions and approved-fact lists for consistency, then score the supplied exploratory interview outputs. Record input findings as **acceptable**, **needs correction** or **unsupported**, with reasons. Keep this input review separate from output scoring.

Some existing test sources require an unsupported LaTeX engine or fonts not verified in the deployed renderer. Their local previews must not be mistaken for supported production output. Those cases remain explicitly recorded while we prepare a supported, versioned calibration set.

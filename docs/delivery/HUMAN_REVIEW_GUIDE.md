# HireWiz reviewer instructions

Updated 28 October 2026. The current **v7 exploratory interview package** contains 10 fixed synthetic cases, **18 delivered outputs and two explicitly unavailable outputs**, with two blank scoring sheets. Each case keeps both A/B slots; failed generations have not been retried, replaced or removed. The v5 and v6 packages remain separate historical batches. None is an approved reference or a release qualification.

## Purpose

Help us check whether HireWiz can lower its costs while keeping the quality of its résumé and interview services. Your job is to judge the results against the candidate’s supplied information and the job description. You are not deciding whether the candidate should be hired.

## People needed

- **Reviewer 1 and Reviewer 2:** two different people with résumé, recruiting or relevant professional experience. Both review each output independently.
- **Adjudicator:** a third person who resolves disagreements using the source information and the reviewers’ written reasons.
- **Coordinator:** prepares the files, keeps the hidden A/B identities and collects the sheets. Someone who knows those identities should not score the outputs.

The project owner has volunteered as **Reviewer 1**; no scores have been submitted. Reviewer 2 and the adjudicator are not assigned. Use your actual name or an assigned private reviewer ID. Do not use an AI system to fill in the scores. The coordinator must keep the A/B mapping hidden from every scorer, including the owner.

## What you will receive

For each case: the original résumé, the job description, a list of facts approved for use, the exact service requested, and two slots labelled **A** and **B**. A delivered slot contains the actual interview output. Each of the two failed slots contains an **Unavailable — generation failed** notice instead of generated content. `output-status.csv` lists all 20 slots and their availability.

This package evaluates interview questions only. The other service checks below are guidance for later packages. A future résumé-tailoring package must also include the original and changed document files and page previews.

You will not receive the model names, service prices, which output is cheaper, or the other reviewer’s scores. A/B are anonymous labels; do not assume A is the old version or B is the new one.

If a source, page or expected delivered output is missing, unreadable or reveals a hidden identity, stop that case and tell the coordinator. Leave its scores blank and explain why. For each explicitly unavailable slot, leave all four scores and all critical-problem flags blank and note that no deliverable was produced. Continue reviewing the available counterpart. Do not invent scores, enter zero, mark it as a successful refusal, replace it with an older output or remove its row. Failure remains part of the overall delivery report.

## Review one case

1. Read the original résumé, job and approved facts before reading the outputs.
2. Review A on its own. Check the facts and the specific service requirements below.
3. If A was delivered, give it four scores and a concrete reason. Record any critical problem separately. If it is explicitly unavailable, record that fact and leave scores and defect flags blank.
4. Repeat for B using exactly the same standard.
5. Save your own sheet. Do not compare scores with the other reviewer before submitting it.

Do not edit an output to repair it. Record what needs fixing. Do not copy actual customer information into a public AI tool or public document.

## Check the guidance as well as the questions

An exact quote from the résumé does not make the surrounding advice factual. Look for guidance that quietly assumes a particular achievement, tool, responsibility or outcome that the approved facts do not establish.

These are illustrative examples, not findings about any A/B label:

- Evidence: “Used a spreadsheet tool on a reporting project.” Unsupported guidance: “Explain how your automation reduced costs by 20%.” The source establishes neither automation nor that result.
- Evidence: “Worked with a reporting system.” Unsupported guidance: “Describe the system you built.” Using a system does not establish that the person built it.
- A tool is absent from the supplied facts. “You have no experience with that tool” is an unsupported personal claim. “If you have relevant experience, describe a verified example; otherwise explain your approach” is conditional guidance.

Ordinary questions such as “How would you approach this problem?” do not assert that the person has already done the work. Distinguish such questions and genuine conditionals from statements that present unverified experience or achievements as facts. Record the exact wording and missing support; do not assume the candidate is either capable or incapable based only on omitted information.

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

## Current review batch and return instructions

The v7 package contains **10 fixed development cases with preflight-checked native PDFs**. Each reviewer receives 20 rows: **18 delivered outputs to assess and two unavailable slots to record without scores**. There are 40 independent review rows across both sheets, not 40 delivered outputs. Both failed generations remain visible in the package and reporting. This small batch helps find problems and is not a final quality certification.

The coordinator supplies the actual outputs and pair IDs with each versioned package. Each reviewer fills only their own CSV and returns it privately to the coordinator, identifying any incomplete cases. Keep the supplied column names and A/B labels. Do not exchange sheets with the other reviewer or mix scores from different output versions.

Later, we will use a separate held-out set that was not used to improve the system. A qualified analyst checks the planned statistical comparison. Human scores alone, or this first batch, do not automatically authorize paid launch.

## Work available now

Reviewers can examine the synthetic source résumés, job descriptions and approved-fact lists for consistency, then assess the 18 delivered exploratory interview outputs. Record input findings as **acceptable**, **needs correction** or **unsupported**, with reasons. Keep input review separate from output scoring and keep both unavailable slots in the returned sheet.

Some existing test sources require an unsupported LaTeX engine or fonts not verified in the deployed renderer. Their local previews must not be mistaken for supported production output. Those cases remain explicitly recorded while we prepare a supported, versioned calibration set.

## Earlier exploratory batches

The v6 package remains unchanged: it had 10 synthetic cases, 19 delivered outputs, one unavailable output, 20 provider attempts and no human scores. Its source-triage and costs are recorded separately in `docs/delivery/evidence/2026-10-28-reviewer-kit-and-capacity.json`. Do not transfer scores or model labels between versions. The v5 package is also retained independently.

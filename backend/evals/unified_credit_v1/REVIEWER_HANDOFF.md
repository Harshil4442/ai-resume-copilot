# Independent reviewer handoff

No reviewer has been assigned or recorded as having approved this packet. Codex can prepare files, check schemas/hashes, identify likely defects and organize results. It cannot sign as a human reviewer, invent reference judgments or fill the independent-review columns using another model.

## Who does what

The product owner can arrange qualified colleagues or external reviewers; external hiring is not assumed or authorized. Assign two distinct people with resume-review/recruiting and relevant role expertise, plus a third person able to adjudicate disagreements. Reviewers score independently, cannot see each other's scores and must not know which model/workflow produced A or B. A coordinator who knows the A/B mapping should not score those outputs. Use actual authorized reviewer identities in the private evidence records. Separate financial and release approval remains the owner's responsibility.

An input reviewer first checks whether the experiment is meaningful. No model output is needed for that work. A statistician or suitably qualified analyst separately reviews the proposed powered held-out analysis; the current draft does not claim that 200 pairs automatically provide sufficient power.

## Review synthetic inputs now

Open `development-execution-scope.json`, each selected native source and its unchanged `case.json`. There are 40 development cases: 10 per format. Check that the role/seniority and experience years are internally consistent; all claims, metrics and employer/education names are fictional; the source text is complete; and the missing job skill is genuinely not claimed. Verify that the approved/unapproved evidence distinction is clear. An unapproved fact can remain in the original resume but cannot automatically support a new strengthened claim.

Check numbers, section order, columns, tables, fonts, margins, links and long-document flow against `source_recipe`. Record any missing source, failed native render or unsupported engine/font as such. The current production compiler does not support the frozen XeLaTeX sources, and its DOCX image lacks the frozen fonts. Do not approve local materialization as proof of deployed support. Review any proposed replacement calibration corpus as a new version before it is generated/scored; retain the original unsupported cases and their expected-refusal purpose.

Use a separate input-review record containing: case ID, frozen case/native hash, reviewer ID, review time, acceptable/not acceptable/unsupported, precise issues, and proposed changes. Do not edit the frozen v1 files or fill a passing reviewer field simply to unblock execution. Approve or reject the experiment scope, request templates, reference-selection method and rubric explicitly. No output-quality score belongs in this input review.

## Review blinded outputs later

The coordinator supplies the source resume/job, approved evidence, an opaque pair ID and outputs A/B. The coordinator keeps route/model/cost identities private and first checks the presentation copies for identity leaks. Review both outputs independently using the frozen `rubric.md`. If the packet lacks source material, has leaked model identity or contains an unreadable artifact, stop that case and report the problem rather than guessing a score.

For each output enter relevance, completeness, clarity and usefulness scores from 1 to 5, with a specific written reason. Confirm eight distinct interview questions, actionable guidance, supported personal claims, useful edits, honest uncertainty and future-tense learning tasks. Citations must actually support claims. “More fluent” does not justify an invented metric, tool, responsibility or achievement.

For tailoring, inspect every source/output page and the native download: unchanged section order, typography, protected content, links and reading order; no clipping, overlap, missing pages or silent template switch. The reviewed and downloaded hashes must agree. An unchanged source snapshot is not successful tailoring. Correctly refusing an unsupported file is a refusal outcome, not a successful deliverable-quality score.

Mark factual error, privacy error and document corruption explicitly true/false. Any critical defect blocks release until independently resolved. Do not average it away with otherwise high scores. Missing evidence remains incomplete; no default passing score is permitted.

There are 80 outputs and **160 independent review rows per operation** for the 40 development pairs. All seven AI operations would require 1,120 review rows, not 160. Start with the supported PDF development subset to identify problems early. These development judgments can inform improvements; they do not replace the later held-out evaluation.

The third reviewer adjudicates every score disagreement using both reasons and original source evidence, recording the case/pair/label/metric, resolved score and explanation. They must not choose whichever score makes the candidate pass. The analyst then uses only the prespecified held-out results and the approved statistical method; neither Codex nor an AI grader can stand in for these human judgments.

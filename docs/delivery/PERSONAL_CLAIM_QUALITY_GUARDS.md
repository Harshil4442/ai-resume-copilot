# Personal claim validation and output contract binding

This change addresses demonstrated validation gaps. It does not qualify a model,
activate an AI route, authorize provider spending, or replace blinded review.

## Preserved deliverables

The interview contract still requires eight distinct questions and two to four
preparation steps per question. Source-format, numeric, glyph, native/render and
candidate-review controls remain in place. No guard rewrites, clips or substitutes
generated text. Unsupported claims reject the affected deliverable/proposal using
the existing failure path; customer credits are restored without erasing supplier
expense or unknown financial liabilities.

Useful grounded personalization remains supported. For example, an approved
Python implementation can support a normal Built/Developed paraphrase; an actual
approved leadership assertion can support the corresponding leadership statement.
Neutral methods, future projects and genuine conditional guidance remain usable.

## Explicit claim scopes

`personal_claims.py` recognizes a finite English-language set of predicates for
leadership, ownership, completed work, outcomes, seniority, tenure, education,
credentials and employment. It checks canonical action subtypes and local object
anchors against **one** supporting assertion, rather than pooling words from
unrelated facts. Designing does not establish deploying; mentoring does not
establish managing. A skill tag does not establish a particular implementation
or let a metric be attached to unrelated work. Explicit negative, conditional,
planned, other-actor and collective-leadership statements cannot establish an
unqualified affirmative candidate assertion. Current employment cannot be inferred
from an explicitly past employment fact.

For tailoring/bullets, the original passage and actually cited approved facts are
the support boundary. Q&A checks every answer/follow-up claim against its cited
approved facts; job and derived-match citations do not establish personal history.
Learning plans check every prose field, including nested descriptions, reasons,
steps and timeline text, against candidate-approved facts. Interview-generated
coaching does not assert these personal facts at all; approved facts remain
quoted separately. Legacy AI-unit adapters use the same guard before completing
their billable context, so they cannot provide an alternate unvalidated path.

This finite lexical/grammar guard is **not semantic entailment**. Unrecognized
wording, more complex scope, synonyms and other languages can need further
evaluation; conservative refusals can also occur. There is no claim of universal
factuality. The existing reference, cohort, native-format, human comparison and
economic gates remain required before any route is sold.

## Version and qualification boundaries

| Contract | New version |
|---|---|
| Interview prompt / validator | `interview-evidence-v9` / `interview-complete-v4` |
| Legacy / complete tailoring prompts | `resume-source-v8` / `resume-source-v9` |
| Tailoring personal-fact validator | `resume-personal-facts-v2` |
| Synchronous prompt / validator | `sync-evidence-v2` / `sync-complete-v2` |
| Shared personal-claim rules | `personal-claim-scopes-v1` |

The six affected unified routes are `resume_tailor`, `resume_tailor_extended`,
`bullet_rewrite`, `enhanced_qa`, `learning_strategy` and `interview_questions`.
Their reviewed `QualifiedRoute` needs an explicit `output_contract_sha256` equal
to `delivery_contracts.current_output_contract(operation)` before a new quote or
execution is admitted. The digest covers actual related prompt builders,
validators, shared helpers, regex patterns/flags, alias constants and declared
versions. Source-introspection failure refuses new work cleanly. Unrelated
discovery, enhanced matching, enrichment and market routes are not newly qualified
or disabled by this rule.

An optional field permits old stored quotes/history to remain readable. No old
JSON review is automatically filled in, requalified or overwritten. Existing
financial snapshots, settlement/refund terms and unknown-cost holds are preserved.
An accepted old route cannot dispatch a new model request under changed output
code: provider preflight/attempt admission and even reuse of an alternate active
budget enforce the same binding. Reconciliation and refunds remain possible.

Prompt registration freezes the actual interview prompt-building helper and
contract digest, so an unversioned helper/constant change is refused. Semantic
reuse/quote fingerprints include the new versions and current contract digest.
Historical authorization versions remain readable but cannot authorize new work.

Recording a digest only identifies code. New quality evidence, reviewer approval
and funded release authorization are still necessary; a matching digest alone
never creates qualification.

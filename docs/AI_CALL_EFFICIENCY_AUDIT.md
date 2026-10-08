# HireWiz AI call efficiency audit

Audit date: 8 October 2026. Code baseline: `d5c2ea8`. Implementation status: current behaviour is distinguished below from proposed changes. Hosting remains on the existing GCP deployment.

Use deterministic extraction, retrieval, scoring, validation and workflow execution by default. Reserve generative calls for useful interpretation and writing that pass quality and cost evaluation. The proposed discover → choose original/custom resume → fill confirmed facts → submit an approved package → track receipt path should work without an LLM. Required free-text answers can be supplied by the candidate when AI drafting is disabled.

## Meaning of avoiding AI calls

| Category | Meaning | Examples |
| --- | --- | --- |
| Deterministic processing | No trained-model inference or generative API request | Regex, native file parsing/rendering, dates, dictionaries, SQL/full-text retrieval, configured scores, templates and state transitions |
| Local learned processing | No external LLM request, but still model inference | Existing spaCy PERSON extraction; possible future learned OCR |
| Reused AI output | Avoids a new request; content is still AI-derived | An unchanged saved match explanation or tailored draft |
| Optional generation | A bounded provider request where meaning or writing adds value | Nuanced role analysis, tailored wording and reviewed motivation answers |

Embeddings are AI processing even when generated locally. A vector database is storage/search infrastructure, not an embedding generator. External job-data requests, native processing and storage can cost money without being AI calls. Product analysis units are not a count of model requests.

## Current workflow findings

The audit traces registered application routes and their helpers. A mounted legacy endpoint can still execute even when its former frontend page redirects. Conversely, a helper definition alone is not a current user-facing call. No live traffic savings or provider-attempt totals are asserted from this static audit.

| Current feature | Actual current model use | Proposed efficient treatment |
| --- | --- | --- |
| Resume upload | Skills extraction makes one logical generation request. PDF/DOCX text and sections, regex contact fields and date calculations run locally; name extraction may use local spaCy | Make known skill extraction deterministic first, preserve source spans/corrections, and enrich only useful unresolved cases |
| Job matching | One combined generation supplies JD skills, transferable coverage, eight dimensions and coaching; final weighted scoring is local | Provide deterministic initial fit and explanations; offer optional enhanced semantic analysis; benchmark the changed score contract |
| Native resume tailoring | Usually one generation, with bounded validation-repair requests | Keep generation for useful wording; select relevant evidence before prompting, reuse drafts and perform all native format checks locally |
| Source snapshots and exports | No generative call; original snapshots retain source bytes, native exports apply saved edits | Keep local; seal the reviewed artifact once rather than repeatedly rendering approved versions |
| Interview questions | Generation produces role questions/coaching, with a possible format/count repair; answer scaffolding and evidence states are local | Default to a curated role/skill question bank and templates; optional AI for new scenarios/personalization |
| Public bullet tool | Generation is used for action-verb score, number presence and a rewrite | Make checks/rubric local; offer an evidence-constrained rewrite as a separate optional operation |
| Authenticated bullet rewrite | Registered API generates text; no current frontend caller found | Consolidate behind the validated evidence-based editing path rather than maintaining a second weakly validated generator |
| Learning resources | Basic skill gaps/course lookup are deterministic; registered match-strategy endpoint generates a plan with an existing template fallback | Make curated resources and project templates the default; generate only an optional personalized brief |
| RAG answers | Registered ask endpoint generates every answer; retrieval is lexical TF-IDF-like scoring and rule-based intent detection | Answer supported factual questions from stored fields first; reserve generation for open-ended synthesis |
| Market analysis and project suggestions | No model calls; taxonomy extraction, frequencies, fixed weights and templates | Keep deterministic; improve taxonomy, source quality and coverage |
| Career Memory | User-approved structured CRUD; no memory generator/consumer found | Use explicit stored preferences/answers directly |
| Skill ROI | Fixed arithmetic over opportunity demand, approved evidence and outcomes | Keep explainable; label as a prioritization heuristic, not a learned or causal return prediction |
| Evidence import/edit/approval | Section-based import and database operations; imported skills can originate from prior AI extraction | Keep non-generative; retain provenance, improve claim grouping and require candidate review |
| Dashboard, activity, outcomes, reminders, contacts | Database operations, events and aggregation | Keep deterministic; reading previously generated results does not initiate generation |
| Authentication, billing, usage, permissions | Cryptographic checks, durable ledger and state transitions | Keep outside model authority |
| Public score/keyword/JD audit pages | Static guides/checklists; the bullet tool is the executing public tool | Do not describe those guide pages as existing automated analyzers; future checks can use local rules |

Evidence for current call paths: [upload](../backend/app/routers/resume.py), [parser](../backend/app/services/parsing.py), [analysis operations](../backend/app/domains/analysis/operations.py), [generators](../backend/app/services/llm_client.py), [public bullet tool](../backend/app/routers/public_endpoints.py), [learning](../backend/app/routers/recommendations.py), [learning templates](../backend/app/services/recommender.py), [RAG retrieval](../backend/app/services/rag/retrieval.py), [RAG answers](../backend/app/services/rag/chat.py), [market analysis](../backend/app/services/market/analyzer.py), [career state and Skill ROI](../backend/app/domains/career/service.py), [public pages](../frontend/app/tools/[slug]/page.tsx).

## Concrete current issues to address

1. **Repeated unchanged work can generate again.** Analysis idempotency reuses a supplied key, but current generation actions create fresh UUIDs. Add content/dependency-based reuse and coalesce identical active work; request-key idempotency alone does not prevent the same analysis with a new key. Keep an explicit generate-another-version action. [Run creation](../backend/app/domains/analysis/service.py), [workspace actions](../frontend/app/workspace/[id]/page.tsx)
2. **Do not simply enable the current parser fallback globally.** Its substring vocabulary can recognize Java inside JavaScript or the language R inside ordinary words. Reuse the existing bounded taxonomy extractor, then improve short-token context, non-technical vocabulary, negation and source spans. Unknown terms remain unknown rather than false skill gaps. [Parser fallback](../backend/app/services/parsing.py), [bounded extractor](../backend/app/services/market/skill_extractor.py)
3. **Public bullet failure invents an accomplishment.** The fallback returns fixed text about leading development and improving throughput. Replace it with the original bullet and explainable local feedback. A number in text is not proof of a verified impact metric; distinguish dates/versions from useful quantities. [Public fallback](../backend/app/routers/public_endpoints.py)
4. **Current holistic scores depend on model output.** Removing the call requires a new honest basic-fit schema/mode, not fabricated substitutes for eight dimensions. The combined prompt also truncates JD/work/projects and requests an education dimension without including education. Compare rules and enhanced analysis against human-rated examples. [Combined analysis](../backend/app/services/llm_client.py), [score assembly](../backend/app/domains/analysis/operations.py)
5. **Repair, provider fallback and worker retries can multiply requests.** Count every real attempt, including unsuccessful/repair calls, under one persisted per-run budget. Current operation telemetry uses approximate tokens and configured model identity; it does not fully count nested attempts. Prefer returned provider usage and actual successful model, labeling estimates. [Provider client](../backend/app/services/llm_client.py), [worker retry handling](../backend/app/domains/analysis/tasks.py), [telemetry](../backend/app/domains/analysis/operations.py)
6. **Dormant helpers are not present usage savings.** The separate JD-skills/fit-summary/holistic helpers and the apparent twelve pairwise calls behind the unused `compute_match_score` path have no routed application callers. Cleanup can simplify maintenance, but removing them does not establish lower current call volume. Legacy mounted LaTeX/rewrite/learning/RAG routes require separate compatibility decisions. [Matching helpers](../backend/app/services/matching.py), [legacy jobs route](../backend/app/routers/jobs.py)

## Planned workflow without mandatory generation

| Action | Default implementation without LLM | When AI may add value |
| --- | --- | --- |
| Verify employer origin | Verified domain/board registry, DNS/redirect checks, grant records and human review | Suggestions cannot establish trust or permission |
| Fetch jobs | Documented APIs/feeds, structured data and tested parsers | None required |
| Freshness and closure | Source dates, successful scan state, conditional requests and explicit status | None required |
| Normalize fields | Provider mappings, country/currency/location dictionaries and taxonomy aliases | Selectively interpret unresolved narrative fields, with confidence/provenance |
| Deduplicate | Provider/requisition IDs, normalized URLs and content hashes | Optional semantic duplicate suggestions; never merge solely on model similarity |
| Extract resume text | Native PDF/DOCX parsing and source structure | Ambiguous narrative grouping can receive optional enrichment |
| Parse dates/experience | Month-aware dates, interval union, context and candidate corrections | None needed for arithmetic; ambiguous periods stay reviewable |
| Identify skills | Bounded term/alias catalog with source spans and context | Unfamiliar terminology or implied responsibilities; no invented capability |
| Confirm facts/preferences | Structured forms, explicit values and versioned records | Optional related-role suggestions |
| Evaluate eligibility | Explicit country/work-authorization/sponsorship constraints | Never infer sensitive/legal declarations |
| Retrieve and rank | SQL/full-text retrieval, configured role/skill weights and evidence coverage | Optional enhanced semantic comparison after measurable relevance gain |
| Explain fit | Templates populated with actual strengths, missing evidence and preferences | Nuanced interpretation/coaching |
| Select original/custom | Immutable bytes, file validation and explicit per-job selection | None required |
| Prepare tailored wording | Local checks, user edits and approved terminology suggestions first | Useful truthful writing; generation remains appropriate here |
| Preserve layout/export | Native edits, geometry/font/rendering checks, hashes and file limits | None required; model judgment cannot certify layout |
| Map known forms/reuse answers | Adapter field IDs, scoped question schemas, confirmed values and conditional rules | Optional reviewed free-text drafts; unknown fields can require handoff |
| Autofill and submit | Tested adapters, recipient/package authorization and durable attempts | None; model must not improvise recipients or final actions |
| Reconcile and track | Provider receipts/lookups, explicit user reports and state transitions | None; model guesses cannot establish acceptance |
| Billing/security/operations | Ledger, permissions, quotas, outbox, redaction, alerts and templates | Optional redacted summaries only, outside decision authority |
| Interview/learning support | Curated questions, evidence templates, skill gaps and resource/project catalogs | Optional custom scenarios and personalized project briefs |

Automatic execution still requires actual integration permission and a complete approved form. A zero-LLM path does not remove those requirements. Generic form questions, employer declarations and candidate facts must not be treated as equivalent just because words overlap.

## Where generation remains useful

Retain optional AI for meaningful resume wording improvements, interpreting ambiguous responsibilities, nuanced transferable-experience analysis, custom interview scenarios, open-ended career discussion and motivation/project answer drafts. Candidate-confirmed data and approved facts constrain every draft.

Rule-only replacement should not reduce these features to generic text that conceals missing context. Preserving numbers/names is necessary but does not prove the whole statement is true. Related skills are not interchangeable qualifications. Missing resume mentions mean missing evidence, not proof of inability. Use user review, source references and targeted semantic evaluation where rules cannot settle meaning.

Curated questions/resources must be labeled accurately. Suggested project bullets describe future work until the candidate actually completes and approves that experience; they cannot be imported as achievements. Do not describe a source snapshot with no edits as a successfully tailored document.

## Cache and execution policy

The operation sequence is: validate ownership and input → deterministic preflight → retrieve a valid result or join identical active work → run local processing → optionally admit bounded generation → validate semantics/native output → persist result and accounting. Cached content never bypasses live recipient, form, permission, job-availability or approval checks.

Use dependency fingerprints rather than a single cache key for the entire workflow:

| Layer | Relevant dependencies |
| --- | --- |
| Private resume extraction | Owner, file hash, parser/taxonomy version and candidate corrections |
| Public job normalization | Source/tenant/posting identity, changed-content hash and parser/schema/taxonomy versions |
| Basic fit | Resume/fact/evidence revisions, job version, relevant preferences and scoring/taxonomy version |
| Enhanced explanation | Basic input fingerprint plus prompt/model/version and requested mode |
| Tailored draft | Owner, source hash, approved evidence, job content, selected edits/style, editing/validation/prompt/model versions |
| Interview/learning | Role/job content, relevant evidence/skill gaps and catalog/template or prompt/model versions |

Keep private results within their candidate boundary. Share only public source normalization and governed public catalogs. A location-preference change can reorder candidates' results without reparsing the resume or regenerating wording. Evidence revocation invalidates affected outputs; live approval checks remain authoritative. File hashes identify bytes, not ownership or truth.

Navigation, tab changes, polling, progress refresh, approvals, unchanged downloads and receipt reconciliation must not invoke an LLM. An explicit request for a new writing variant bypasses draft reuse under its own bounded budget. An automatic refresh of metadata does not imply a fresh generation request.

Reduce tokens by selecting relevant source units and facts locally before prompting, retaining source links and enough context to prevent changed meaning. Provider prompt caching may reduce cost/latency but is not the same as application-result reuse: it still involves a provider request. Do not claim savings without actual attempt/token measurements.

## Development priorities and acceptance

1. Add per-attempt telemetry, immutable-input reuse, active-run coalescing and pending-action guards.
2. Fix the invented public fallback; move bullet diagnostics to local explainable rules.
3. Improve deterministic skills/experience extraction, then make routine upload enrichment optional.
4. Build default fit/filter/explanation paths that work with LLMs disabled; evaluate changes before replacing the current scoring contract.
5. Add curated interview/learning defaults and direct stored-data answers for supported questions.
6. Keep useful tailoring generation, with source/evidence preflight, smaller relevant context and bounded total attempts.
7. Implement discovery, forms, execution and tracking without generative dependency.

| Acceptance area | Required evidence |
| --- | --- |
| Call avoidance | LLM-disabled integration test covers discovery/basic fit/original-custom selection/confirmed-answer filling/supported submission/receipts; unexpected calls fail the test |
| Reuse | Identical concurrent requests produce one execution; unchanged inputs reuse results; changed relevant dependencies invalidate only affected layers |
| Extraction | Representative layouts, short tokens, aliases, non-technical terms, month/date grouping and source-span correctness |
| Ranking | Human-rated top-result relevance, useful jobs missed, false hard exclusions and visible uncertainty; compare with enhanced mode |
| Writing | Useful edits, candidate acceptance, unsupported claims, preserved metrics/dates and native layout; snapshots remain distinct |
| Forms | Complete required/conditional fields and correct recipient/document/consent; no sensitive answers inferred |
| Economics | Actual attempts, tokens, repairs, fallbacks, cache/coalescing savings, latency and cost per useful result |
| Accounting | No duplicate ledger transitions; mode/result provenance and displayed charges are consistent with product policy |

No percentage saving is assumed. The first benchmark establishes calls and cost per completed workflow, then validates that removing calls retains accuracy and usefulness. Non-AI processing still needs capacity budgets and monitoring. The hosting, submission authorization and recovery rules in the [system design](EMPLOYER_DISCOVERY_APPLICATION_SYSTEM_DESIGN.md) remain unchanged.

# HireWiz credit cost and quality requirements version 1.1

Implementation authorization: the owner requested end-to-end implementation, testing, deployment and payment activation on 27 October 2026 after reviewing version 1.1. This supersedes only the document's review-only authorization boundary; its quality, commercial readiness, customer-consent and financial gates remain binding. No historical discount-price evidence is assumed.

Source: `HireWiz_Credit_Cost_and_Quality_Requirements_v1_1.docx`.
Source SHA-256: `ac56be4838c8309fa9b1da3a717d53fab2bad85cfe0482d6aeedc4374a38fb4f`.

The source content follows. Stage progress and requirement evidence are tracked separately.

# HireWiz Credit Cost and Quality Requirements

Affordable services with protected deliverable quality

Version 1.1   |   27 October 2026   |   Proposed for owner review

Prepared for the HireWiz product owner and engineering team. This document consolidates the proposed single credit wallet, revised packs, delivery cost controls, monthly financial scenarios and development acceptance checklist. The owner will review it before implementation begins.

Primary requirement: cost reduction must not reduce the quality of a promised deliverable. A cheaper route must pass the same factual, formatting, completeness and usability requirements as the approved reference route. If it cannot, retain a qualified route, quote the work accurately or decline it without charging.

### Recommended commercial position

| Decision | Proposal |
| --- | --- |
| Credit packs | Offer prices ₹59 for 40; ₹179 for 130; ₹449 for 360 credits |
| Purchase assumption | 10,000 monthly active users; 20% monthly purchasers as baseline; 25% sensitivity |
| Execution funding | At most ₹0.45 per consumed credit, subject to validated workload bounds |
| Shared operating allowance | ₹25,000 planning case; ₹18,000 optimization target; ₹35,000 adverse case |
| Baseline monthly surplus | ₹24,370 at 20% buyers; ₹36,712 at 25%. At ₹35,000 overhead: ₹14,370 and ₹26,712 |
| Authorization | Requirements review only; implementation and commercial activation are separate decisions |

The surplus assumes every buyer purchases one Starter pass monthly and consumes every credit at the execution ceiling. It is founder-operated cash surplus: unbudgeted staff, founder compensation, acquisition and development-agent subscriptions reduce it. Neither the execution ceiling nor the infrastructure allowance has been validated at 10,000 active users.

Revision 1.1: pack prices increase while credit quantities and per-service credit charges stay unchanged. This adds a genuine limited-offer specification and conversion and cost stress tests. Quality gates remain unchanged; a positive bounded scenario is not a guarantee against every possible loss.

### How to review this document

Sections 2 to 4, including 3A, define scope, pricing and offer controls. Sections 5 to 12 specify delivery, quality, billing and infrastructure. Sections 13 to 15, including 14A, contain the financial model and funding gates. Sections 16 to 19 contain the development checklist, rollout controls and approval record. Section 20 lists sources.

## 2 Scope and authorization

S01  Review boundary. This document does not authorize code changes, migrations, wallet conversion, changed prices, provider purchases, deployments, checkout activation or employer submissions. All implementation checklist items start pending. Older development authorizations do not automatically authorize this new pricing and quality program.

S02  Existing product boundary. Retain GCP, Vercel, PostgreSQL on Neon and Upstash. The documented release context is the bounded employer-origin pilot with 20 reviewed origins and Razorpay or Greenhouse browser assistance. Recheck deployed capabilities in Stage 1; historical documents are not fresh production evidence.

S03  Deferred services. Worldwide expansion, any greater-than-95% coverage claim, arbitrary portal automation and final automatic submission require separate scope, permission and acceptance work. The future submission tariff in this document is not a statement that submission is enabled.

S04  Candidate control. Each opportunity permits the original resume, an approved tailored resume or a custom upload. Approval binds the exact destination, file hash, answers, form schema and intended action. Changed inputs require fresh approval. A development approval never substitutes for a candidate application approval.

S05  Quality priority. No optimization may silently change the promised question count, factuality, resume layout, supported language, accessibility or review controls. Product scope and pricing may change only prospectively and transparently, with existing obligations preserved.

### End to end product journey

Upload and scan the source → extract and confirm facts → collect structured preferences → retrieve verified employer jobs → rank with basic matching → optionally request AI interpretation → choose a resume → optionally tailor and review edits → optionally prepare interview questions → review supported form assistance → record a verified outcome or an explicit unknown state.

The selected-job journey is configurable. Optional enrichment, enhanced matching, tailoring and interview preparation are separate choices; selecting an original resume must not force an AI purchase. Candidate answers and personal declarations remain under candidate authority.

### Relationship to earlier requirements

This proposal extends the prior HireWiz Job Discovery and Application Requirements and AI call efficiency audit. It preserves their origin-verification, immutable-source, evidence, authorization and recovery rules. The new pack sizes and financial policy replace only the earlier pricing proposals when the owner approves and implementation is authorized. See sources L1 and L2.

## 3 Credit packs and commercial policy

| Pack | Offer price | Credits | INR per credit | Proposed standard price |
| --- | --- | --- | --- | --- |
| Starter | ₹59 | 40 | ₹1.475 | ₹79 |
| Growth | ₹179 | 130 | ₹1.377 | ₹229 |
| Scale | ₹449 | 360 | ₹1.247 | ₹599 |

Offer prices include assumed sales GST. Compared with version 1.0, prices rise by ₹10, ₹30 and ₹50, or 20.41%, 20.13% and 12.53%. Credit quantities stay at 40, 130 and 360. Standard prices are future proposals, not evidence of prior sales. All stress forecasts use the lower offer prices; Section 3A governs any crossed-out comparison.

B01  One wallet. Use one customer credit denomination for eligible paid actions. Preserve separate internal monetary accounting for tax, delivery obligations, refunds, incidents and overhead; those are not extra customer currencies.

B02  Validity and renewal. For new purchases, propose 30-day prepaid service passes with no automatic renewal. Show the exact expiry timestamp, included credits, supported services and refund terms before payment. Use the earliest-expiring eligible purchased balance first. Existing balances retain their original promises.

B03  Expiry safeguards. Expiry does not delete original files or invalidate an already funded and accepted operation. A documented retention and export policy applies separately. The economics assume full redemption, not profit from expiration. Legal and product review of validity and refund terms is required before sale.

B04  Transparent quotes. Before execution, show the service, credit charge, selected file and job, expected deliverable, supported limits and any additional quote. Reserve the accepted charge and do not increase it mid-operation. A materially changed input needs a new quote and confirmation.

B05  Reuse and failure. Viewing, downloading and unchanged result reuse do not incur another generation charge. A deliberate new variation is quoted separately. Failed or incomplete deliveries restore credits; provider spending remains recorded. Restored credits must remain usable for at least seven days after restoration, including post-expiry failures, or receive an appropriate monetary refund under the approved policy. Prevent repeated failure loops.

B06  Payments and migration. Verify gateway signatures, use idempotent payment processing and maintain an auditable ledger. Define an owner-approved conversion for any legacy wallets before migration; do not infer equivalence between old analysis units and new credits. Reconcile every account and retain rollback evidence.

Paid sales require a permitted commercial hosting plan and separate activation approval. Prelaunch operation is not evidence of provider eligibility. This plan does not purchase a Vercel upgrade or activate checkout. [4]

## 3A Limited offer display and lifecycle

Propose one 30-day launch campaign beginning on the separately approved paid-launch date. Its calendar start and end must be fixed before publication. This is independent of each purchased pass lasting 30 days. The pricing screen remains a proposal; no offer starts with this document.

### Conditional comparison layout

Use the crossed-out layout below only after the standard prices are genuinely established for the same pack and market, the reference evidence is reviewed and the campaign is approved. These are design examples, not claims that HireWiz previously sold at these prices.

| Pack | Established standard price | Limited offer | Credits | Saving |
| --- | --- | --- | --- | --- |
| Starter | ₹79 | ₹59 | 40 | ₹20 |
| Growth | ₹229 | ₹179 | 130 | ₹50 |
| Scale | ₹599 | ₹449 | 360 | ₹150 |

P01  Authentic reference price. Keep evidence of the same pack being genuinely offered at the standard price, its dates, market, terms and actual transactions where available. Finance and local-market review must approve comparison eligibility. Never invent a former price or label a planned future price as a past price. [9][10]

P02  First launch display. Until P01 is met, show Introductory price ₹59 with 40 credits, and the real offer end date; optionally state Planned standard price ₹79 from the disclosed date. Apply the same wording to the other packs. Omit strikethrough, was pricing and savings claims until substantiated.

P03  Real deadline. Store one campaign ID, start, end, timezone, eligibility and status on the server. No resetting countdown, fake stock, fabricated buyer count or perpetual extension. At expiry remove the badge and restore the approved standard price, or disable new checkout if activation is not ready. [10]

P04  Complete purchase terms. Show total INR payable, credits, pass expiry, supported services and no automatic renewal beside the purchase action. Use Buy credits or Buy 30 day pass. Do not imply recurring subscription billing; recurring plans need separate consent and cancellation design.

P05  One price authority. Version prices and campaign eligibility on the server. Quotes, purchase page, gateway order, invoice and ledger must agree. Honor a valid price quote for a proposed 15 minutes even across campaign expiry; then require a new quote. Validate again when creating the payment order.

P06  Fund and test promotions. Do not stack extra coupons or free credits into these forecasts. A new discount needs its own funded margin check. Test campaign start and end, timezone, stale tabs, duplicate payments, refunds, accessibility and historical invoice preservation before release.

Quality and support scope are identical at offer and standard prices. Review local-market requirements before sale.

[9] CCPA misleading advertising guidance

[10] CCPA dark patterns guidance

## 4 Service catalogue and execution allowances

Customer equivalents use the offer packs. Each allowance is a proposed admission ceiling, not a measured provider bill. Qualify the complete route before offering it. Amounts include incremental paid model and worker processing; shared expenses are accounted for separately and never counted twice.

| Service | Credits | Customer equivalent | Execution ceiling |
| --- | --- | --- | --- |
| Optional resume enrichment | 2 | ₹2.49 to ₹2.95 | ₹0.90 |
| Enhanced job comparison | 3 | ₹3.74 to ₹4.43 | ₹1.35 |
| Eight interview questions | 5 | ₹6.24 to ₹7.38 | ₹2.25 |
| Standard resume tailoring | 8 | ₹9.98 to ₹11.80 | ₹3.60 |
| Higher cost tailoring | 15 | ₹18.71 to ₹22.13 | ₹6.75 |
| Bullet rewrite | 2 | ₹2.49 to ₹2.95 | ₹0.90 |
| Enhanced personal Q and A | 2 | ₹2.49 to ₹2.95 | ₹0.90 |
| Personalized learning strategy | 4 | ₹4.99 to ₹5.90 | ₹1.80 |
| Up to five new official jobs | 1 | ₹1.25 to ₹1.48 | ₹0.45 |
| Verified submission when enabled | 3 | ₹3.74 to ₹4.43 | ₹1.35 |
| Fresh indexed market report | 1 | ₹1.25 to ₹1.48 | ₹0.45 |

T01  Defined deliverables. An interview purchase means eight distinct questions with guidance. A standard tailoring purchase means one validated edited version within a published format and edit envelope. Bullet and Q and A operation limits must be displayed and benchmarked; they cannot imply unlimited conversation.

T02  Expensive work. Fifteen credits is a quoted example, not a universal price for all complex documents. If a qualified route exceeds its allowance, quote a separately approved charge or decline without executing. Never apply a generic template to claim format preservation.

T03  Discovery and submission accounting. Charge discovery only for a completed quoted batch containing at least one new verified result; show partial counts and do not count duplicates or closed jobs as new. Submission is billable only after authoritative confirmation. Pending or unknown outcomes require reconciliation, not a blind retry.

Example: one discovery batch, enhanced comparison, standard tailoring, an interview set and future verified submission total 20 credits, equivalent to ₹24.94 to ₹29.50. The Starter pack supports two such journeys. Application capability remains separately gated.

## 5 Efficient processing without reduced value

E01  Extract once. Use native PDF, DOCX and supported LaTeX extraction with source spans, versioned parsers and candidate corrections. Apply dictionaries and contextual rules to known skills and dates. Ambiguous facts remain reviewable; absence of a keyword does not prove absence of a skill.

E02  Shared official job ingestion. Use the verified employer registry, documented feeds or permitted adapters. Fetch once for many users using conditional requests and changes since the last successful scan. Deduplicate by source ID, requisition, canonical URL and content revision. Record freshness and coverage limits.

E03  Basic matching and calculation. Use explicit eligibility rules, weighted skill coverage and database or lexical search. Keep date arithmetic, Skill ROI, market aggregation and profile CRUD deterministic. Label heuristic scores honestly; do not fabricate richer AI dimensions when the enhanced route was not used.

E04  Optional generation. Preserve AI where it adds useful interpretation or writing: nuanced comparison, supported rewriting, role-specific questions and personalized plans. Do not replace a paid personalized output with generic text solely to save money.

E05  Context selection. Send relevant approved evidence, the exact job revision and requested edits. Limit unrelated history and duplicate source text. Validate that evidence selection did not omit a fact needed to meet the promised deliverable before admitting the model call.

E06  Correct reuse. Private cache keys include owner, source hash, approved evidence and correction versions, job revision, preferences, prompt, model, schema and validator versions. Share only public job data. Invalidate meaningful changes and coalesce identical active work. Cached results never bypass fresh authorization or job-availability checks.

E07  Qualified model routing. Evaluate Flash-Lite for simpler work and keep a qualified stronger route for demanding tailoring. The cheapest route is not automatically the default. Every route must pass Section 8. Use explicit bounded reasoning and output settings, with all provider attempts counted. [1]

E08  Controlled background work. Use queues for suitable noninteractive work, adaptive polling and targeted refreshes. Deferred or batch generation is allowed only when its delivery timing was disclosed and accepted; never make an interactive flow slower without a product decision.

### Terms that must remain accurate

A deterministic algorithm has no learned inference. Local NLP or learned OCR can still be AI even without a Gemini bill. Reusing generated content avoids a new call but does not make the content non-AI. Ordinary APIs, parsing, storage and browser assistance still consume infrastructure.

## 6 Quality requirements for resumes and facts

These requirements are non-negotiable release gates. A spending limit determines whether we can offer a request; it does not authorize a lower-quality deliverable.

Q01  Approved reference baseline. Freeze approved reference outputs, evaluation data, prompts, model versions, rubrics and validators before optimizing. Existing broken behavior cannot serve as an acceptable baseline. Define the supported format, language, layout and edit envelope before sale.

Q02  Evidence and factual accuracy. Every changed personal claim must reference candidate-approved evidence. Preserve the meaning of names, employers, dates, education, qualifications and numeric achievements. No invented metrics, experience or skills are allowed. Zero unsupported or fabricated claims in release evaluation; any critical failure blocks the route.

Q03  Immutable original. Retain the original upload with a checksum and ownership record. Never overwrite it. A preview, conversion or editing failure must preserve the original. An unchanged source snapshot must not be reported or billed as successful tailoring.

Q04  Layout preservation. For supported formats, preserve section order, font treatment, margins, links, page geometry and unaffected content. Permit reflow only inside approved edit regions. No clipping, overlap, missing sections, accidental blank pages or broken links. Pagination or structural layout changes require explicit review rather than silent template substitution.

Q05  Native and rendered validation. Validate both the document structure and rendered pages. Re-extract text, compare approved changes, verify reading order and inspect layouts. The final downloaded artifact must have the same hash as the reviewed artifact. A model cannot certify that a PDF or DOCX layout is safe.

Q06  Machine readability. Keep contact details, headings and content extractable and correctly ordered. Prohibit image-only substitutions and hidden keyword stuffing. Describe checks as ATS compatibility checks; do not promise selection, a universal ATS score or compatibility with every ATS.

Q07  Unsupported inputs. Detect encrypted, scanned, corrupted, unusually complex or unsupported-language inputs before generation where possible. Request a suitable source, offer a clearly described alternative or decline without charging. Do not truncate relevant content silently to stay within budget.

A LaTeX-created PDF is not equivalent to having editable LaTeX source. The product must state which format and edit combinations are supported and must not promise arbitrary PDF format preservation. The user always keeps the original and approves the final version.

## 7 Quality requirements for guidance and applications

Q08  Interview completeness. A paid eight-question set must contain eight distinct, role-relevant questions with actionable preparation guidance. Limited personal evidence may produce job-based questions marked as needing evidence, but must never produce invented personal answers or a one-question deliverable billed as eight.

Q09  Matching accuracy. Hard eligibility rules must pass reviewed fixtures. Explanations must connect actual job requirements to actual resume evidence and distinguish missing evidence from lack of capability. Ranking quality and relevance must meet the approved baseline within every supported cohort. Scores are not hiring probabilities.

Q10  Answers and learning plans. Ground personal answers in approved facts and identify uncertainty. Learning plans must address the selected gap with actionable steps and verified or freshness-checked resource references. Dead or invented links and generic plans that ignore the selected goal fail acceptance.

Q11  Job relevance and freshness. Verify employer origin, preserve source dates and label the last successful refresh. Exclude confirmed closed roles from new deliveries. Immediately before an application action, recheck the posting and destination. Report the supported source population instead of implying worldwide completeness.

Q12  Application fidelity. Use the candidate-selected file and reviewed answers. Unknown fields, changed form schemas, ambiguous declarations, expired jobs or missing authorization stop the affected action. Do not send data to another destination or claim completion without a verifiable receipt. Final submission remains separately disabled until authorized and accepted.

Q13  No silent downgrade. When the qualified route cannot fit its budget, retain a funded qualified route, present a revised quote or decline without charge. Do not quietly omit facts, shorten the purchased deliverable, reduce document fidelity, switch to an unqualified model or replace personalized guidance with a generic fallback.

Q14  Funded recovery. Reserve any permitted recovery before accepting the quote. A stronger-model retry needs both quality qualification and remaining funding. Failed or incomplete deliveries restore credits; the internal expense remains recorded. Repeated failures open a circuit breaker.

Q15  Privacy security and usability. Retain scanning, ownership checks, encryption, account and device protection, audit retention and backups. Prevent cross-user cache access and prompt-injection instructions from source text. Review and payment flows must remain usable on mobile and desktop, with keyboard navigation and readable text.

## 8 Quality evaluation and release gates

V01  Representative evaluation. Create at least 200 consented or synthetic resume and job pairs covering supported formats, layouts, roles and seniority levels. Include long documents, numeric claims, unusual fonts, tables, multiple columns and incomplete evidence. Use at least 30 examples per important risk cohort where feasible; cohorts can overlap. Keep development and held-out evaluation sets separate.

V02  Human judgment. Use two blinded reviewers and an adjudicator for disagreements on relevance, completeness, clarity and usefulness. AI graders may triage but cannot be the sole release authority. Freeze rubrics before comparing routes, and fund reviewer and evaluation costs explicitly.

V03  No tolerated quality reduction. Require candidate outputs to meet the approved reference in every supported cohort. For scored comparisons, use an appropriately powered paired evaluation with zero tolerated degradation and a one-sided 95% confidence criterion. If the result is inconclusive, gather more evidence or retain the reference route; do not call it equivalent.

| Gate | Required evidence |
| --- | --- |
| Factual integrity | No fabricated or unsupported personal claim in evaluation; all changed claims trace to approved evidence. |
| Document fidelity | All supported-format layout fixtures pass structure, extraction and render checks. |
| Complete outputs | Correct question count, required fields and promised sections; no hidden truncation. |
| Relevance and usefulness | Blinded human comparison meets the approved baseline and cohort gates. |
| Authorization and privacy | No unauthorized disclosure, cross-user result, duplicate submission or invented receipt. |
| Economic qualification | The qualified route fits its reserved upper expense under tested input bounds. |

V04  Release and ongoing review. Progress through offline evaluation, shadow accounting, an opted-in canary and wider rollout. A confirmed critical factual, privacy or document-corruption defect disables the affected optimized route immediately. Baseline fallback is allowed only when funded. Keep customer corrections and refunds visible by route and cohort.

Zero critical errors in an evaluation is a release requirement, not proof that future AI outputs are infallible. Candidates still review material edits. Cost and latency results must be reported alongside quality, never substituted for it.

## 9 Monetary admission and failure controls

C01  Estimate the complete path. Before execution, compute a conservative expense envelope from bounded input tokens, maximum output including thinking, provider rates, FX, unrecoverable taxes, worker duration, file size, parsing or conversion and permitted retries. Do not rely on average tokens or an optimistic cache hit.

C02  Atomic reservation. Reserve customer credits and internal money in one durable transaction before dispatch. Check per-operation, account, route and global remaining budgets. Account for all concurrent reservations so parallel requests cannot each spend the same funds.

C03  Bound every attempt. Count SDK retries, validation repairs, worker retries and provider fallbacks under one persisted operation budget. One provider attempt is the default. Cancellation or a client timeout must not release money while the provider may still bill it. Stop worker processes where required; an HTTP timeout alone is not a process-kill guarantee.

C04  Unknown costs and outcomes. Retain a conservative reservation until usage or status is reconciled. Persist provider request IDs, attempt count and estimated versus reported usage. Unknown application outcomes must not be retried as new submissions. Unknown invoices must not be treated as zero cost.

C05  Circuit breakers. Disable the affected paid route after a confirmed critical quality error, repeated failures or insufficient funded headroom. Initial operational thresholds proposed for review are three consecutive execution failures or more than 10% failures in the last 20 attempts. Monetary admission applies even below those thresholds.

C06  Refund expense separation. Restoring a customer balance does not erase Gemini or worker expense. Charge failed attempts to the funded incident allowance, rate-limit abusive repetitions and keep customer-facing explanations accurate. Refund and chargeback obligations take priority over withdrawing profit.

C07  Price and budget freshness. Version the price policy with valid-from and expiry dates. Reconcile supplier prices, FX and invoices regularly and before known increases. An expired or unknown policy blocks new paid execution; it cannot trigger a cheaper unqualified route.

C08  Provider protection. Use eligible provider spend controls and service quotas as a second layer. Leave headroom for in-flight work and fixed resources. Google documents delayed spend-cap enforcement and ongoing persistent-resource charges. Never use a billing shutdown as the only application control. [7]

Keep tax, delivery liabilities, incident reserves, shared operating funds and earned surplus separate. Cash collected at purchase is not immediately profit. Protect already accepted work and customer access before admitting new optional expenditure.

## 10 Architecture and engineering contracts

Retain the Next.js interface and BFF on Vercel, FastAPI APIs and private workers on GCP, PostgreSQL as the source of truth, Redis for bounded caching and rate limits, and object storage for immutable files. Keep domain rules separate from transport and provider adapters. No multicloud or Kubernetes migration is required.

| Record | Required fields and invariants |
| --- | --- |
| Price and pass | Version, price, credit quantity, validity, tax treatment, eligibility and purchase terms. |
| Quote | Owner, operation, inputs and versions, deliverable, charge, qualified route, expiry and max expense. |
| Wallet ledger | Immutable grant, reserve, capture, release, refund and expiry entries; idempotency and references. |
| Expense reservation | Currency, budget period, policy version, amount held, settlement and unknown-cost state. |
| Execution attempt | Run and attempt IDs, provider request ID, model version, tokens, runtime and actual or estimated cost. |
| Artifact and approval | Source hash, evidence and job revisions, edit set, validator report, output hash and candidate approval. |
| Budget period | Committed overhead, outstanding deliveries, incident allowance, spend and funded available headroom. |

A01  Durable transitions. Use a quote and operation state machine: quoted, reserved, running, validated, delivered and settled; or failed, reconciliation required and released or refunded. Delivery and credit capture occur exactly once logically despite at-least-once queue delivery.

A02  Transaction and outbox. PostgreSQL owns monetary state. Use atomic ledger updates, unique idempotency constraints and an outbox for dispatch. Redis may accelerate reads but must not own the authoritative balance. Test concurrent spending, worker crashes, duplicate webhooks and retries.

A03  APIs and versioning. Define authenticated quote, execute, status, result, wallet and history contracts. Validate ownership on every access. Return explicit unsupported, requote, exhausted-budget and reconciliation states. Synchronize generated frontend contracts and preserve compatible legacy reads during migration.

A04  Tooling. Use repository CI, typed API contracts, database migrations, unit and integration tests, browser tests, PDF or DOCX render comparisons, provider usage telemetry and invoice reconciliation. Existing GitHub, GCP and Vercel tools are sufficient for deployment work; document tooling creates review artifacts. No paid plugin is assumed mandatory.

## 11 Shared operating budget

Use the following founder-operated planning envelope for 10,000 monthly active users. It is not an observed invoice or a promise of capacity. Figures use a ₹100 per USD budgeting assumption with applicable unrecoverable provider tax allowance; reconcile actual billing before release.

| Expense | Lean target | Planning allowance |
| --- | --- | --- |
| Vercel commercial frontend | ₹2,400 | ₹2,600 |
| Core API free workflows and shared ingestion | ₹3,500 | ₹5,000 |
| Neon database | ₹5,000 | ₹7,500 |
| Redis cache and rate limits | ₹600 | ₹1,000 |
| Transactional email | ₹2,400 | ₹2,400 |
| Storage backups and downloads | ₹800 | ₹1,200 |
| Security operations builds and monitoring | ₹1,000 | ₹1,500 |
| Domain renewal monthly allocation | ₹200 | ₹200 |
| Administrative and support tools | ₹1,000 | ₹1,600 |
| Ordinary usage headroom | ₹1,100 | ₹2,000 |
| Total | ₹18,000 | ₹25,000 |

I01  No double counting. Exclude Gemini and incremental paid-worker execution here because they are already funded by execution allowances. Allocate actual provider bills once, using service and operation labels. Shared database and API costs must have an explicit allocation rule.

I02  Load assumptions. Benchmark roughly one million authenticated API requests per month plus separately tested peaks; 0.5 to 0.75 average active database compute units; 2 to 4 million Redis commands; and 20 to 50 GB active document storage. These are workload assumptions, not unrestricted entitlements. [2][3][5]

I03  Expense completeness. Founder compensation, hired staff, paid acquisition, one-time quality evaluation and development-agent subscriptions beyond the tools allowance are additional costs. Add them before claiming fully loaded business profit. Domain renewal and actual supplier invoices replace allowances when available.

Keep workers demand-driven, retain a warm API where latency requires it, pool database connections, use indexed pagination and avoid repeated preview conversion. Preserve scanner maintenance and security-journal retention. Commercial sales need suitable hosting; do not assume free plans can serve the paid workload. [4][6]

## 12 Free usage performance and privacy

F01  Bounded free core. Provide basic upload and extraction, basic matching, evidence management, tracking, curated guidance and saved-result reuse within published limits. Initial limits proposed for review are one new parsed resume and ten basic comparisons per free account per 30 days. Supported file-size, page, storage and retention limits require benchmarking and publication before release.

F02  Funded acquisition allowance. Free server processing and promotional credits require a fixed funded allowance inside the approved shared or acquisition budget. A per-user quota alone is insufficient; enforce global admission as well. When exhausted, stop new optional free processing with an honest message or waitlist. Do not remove existing data or skip security checks.

F03  Fair paid experience. Existing saved results remain readable under the disclosed retention policy. New paid work must have sufficient reserved capacity before acceptance. Never silently degrade quality, throttle beyond the promised delivery window or delete files to make a spreadsheet budget fit.

F04  Responsive performance. Keep common interactions usable on mobile and desktop. Proposed acceptance targets are p75 LCP at most 2.5 seconds, INP at most 200 ms and CLS at most 0.1 on the agreed test profile, and p95 common authenticated reads at most one second under the approved load excluding external identity-provider time. Record device, region, network and test conditions; do not assert these targets are already met.

F05  Efficient delivery. Cache eligible public pages and static assets, lazy-load secondary UI, paginate lists, reduce duplicate polling and serve signed authorized downloads directly. Reuse validated previews. A cache must not expose private resumes or bypass expired access rights.

F06  Security and retention. Retain TLS, scoped secrets, scanning, authorization, account and device controls, durable audit requirements and backups. Preserve existing locked security-journal retention. Do not log resume bodies, passwords, tokens or full application answers in cost telemetry.

F07  Notifications. Prefer transactional account and security email plus opted-in bounded digests. Do not send unlimited per-job notifications to free users. Budget deliverability capacity rather than assuming daily free quotas will always suffice.

F08  User communication. Explain basic, curated, reused and AI-generated modes accurately. Show freshness, source coverage, credit price, remaining balance and unsupported cases. Keep financial implementation details out of normal flows unless needed to explain a quote or limitation.

## 13 Financial assumptions and calculation method

| Input | Planning assumption |
| --- | --- |
| Monthly active users | 10,000 |
| Monthly purchase conversion | 20% baseline; 22.5% and 25% sensitivities |
| Purchase frequency | One pass per buyer per month; no assumed repeat top-up |
| Stress pack mix | Every buyer chooses ₹59 Starter with 40 credits |
| Redemption | Every credit consumed at ₹0.45 execution expense |
| Tax and gateway | 18% included sales GST; gateway expense 2.36% of gross collections |
| Risk allowances | 2% of gross collections for refunds; 10% of net receipts for incidents |
| Shared expenses | ₹25,000 planning case; ₹18,000 lean target; ₹35,000 adverse case |
| Profit target | 15% of defined net receipts as the minimum stress gate; 20% normal planning target |

### Reproducible calculation

Let G be customer collections including assumed GST, C be purchased credits consumed in the period, and H be shared expenses. All amounts are INR.

Revenue excluding GST = G / 1.18
Gateway expense = 0.0236 × G
Refund reserve = 0.02 × G
Net receipts N = G / 1.18 - 0.0236 × G - 0.02 × G
Incident reserve = 0.10 × N
Execution expense = 0.45 × C
Operating surplus after reserves = N - 0.10 × N - 0.45 × C - H
Maximum H preserving the 15% net target = 0.75 × N - 0.45 × C
Maximum H preserving the 20% net target = 0.70 × N - 0.45 × C

M01  Accounting meaning. Sales GST is set aside and is not revenue. Reserves are protected funding, not necessarily expenses already incurred. A positive surplus after reserves is before income tax and before any business cost omitted from H. Do not claim that prepaid cash is immediately earned profit.

M02  Assumption integrity. The model assumes 20% to 25% purchase every month, not that they have ever purchased. It assumes full redemption and no savings from cache hits, expiry or temporary provider promotions. All buyers pay the offer rate; no revenue uplift from standard prices is assumed. Supplier taxes, input credits and gateway contracts require professional reconciliation. [8]

The estimate covers the maximum permitted workload under proposed controls, not unlimited attacks, every customer refunding simultaneously or arbitrary provider failure. No pricing formula guarantees profit with no sales and continuing bills.

## 14 Monthly revenue expense and profit scenarios

All figures below are monthly INR, rounded to the nearest rupee. Every buyer purchases Starter and uses all 40 credits at the execution ceiling. Shared expenses are ₹25,000 for all scenarios; the free-user population is included in that allowance.

| Monthly item | 20% purchase | 22.5% purchase | 25% purchase |
| --- | --- | --- | --- |
| Paying users | 2000 | 2250 | 2500 |
| Collections including GST | ₹1,18,000 | ₹1,32,750 | ₹1,47,500 |
| Revenue excluding GST | ₹1,00,000 | ₹1,12,500 | ₹1,25,000 |
| Credit delivery expense | ₹36,000 | ₹40,500 | ₹45,000 |
| Gateway expense | ₹2,785 | ₹3,133 | ₹3,481 |
| Shared expense allowance | ₹25,000 | ₹25,000 | ₹25,000 |
| Total operating expenses | ₹63,785 | ₹68,633 | ₹73,481 |
| Refund and incident reserves | ₹11,846 | ₹13,326 | ₹14,807 |
| Expenses plus reserves | ₹75,630 | ₹81,959 | ₹88,288 |
| Surplus after reserves | ₹24,370 | ₹30,541 | ₹36,712 |

### Sensitivity to shared overhead

| Shared expenses | 20% purchasers | 25% purchasers |
| --- | --- | --- |
| ₹18,000 | ₹31,370 | ₹43,712 |
| ₹25,000 | ₹24,370 | ₹36,712 |
| ₹35,000 | ₹14,370 | ₹26,712 |

At ₹25,000 overhead, surplus is 24.37% and 29.37% of revenue excluding GST at 20% and 25% conversion. At ₹35,000 overhead and 20% conversion, surplus is ₹14,370, or 15.15% of defined net receipts: above the 15% minimum but below the 20% normal target. Only ₹141 of overhead headroom remains above the 15% gate; treat that as a trigger to reduce commitments, not a comfortable buffer.

### Illustrative larger pack mix

| Purchase rate | Gross collections | Expenses plus reserves | Surplus |
| --- | --- | --- | --- |
| 20% | ₹2,17,000 | ₹1,22,555 | ₹61,343 |
| 25% | ₹2,71,250 | ₹1,46,944 | ₹82,929 |

This illustration assumes 70% Starter, 25% Growth and 5% Scale; it is not a validated forecast. Relative to version 1.0 with the same 40-credit Starter and cost assumptions, the price increase adds ₹14,469 monthly surplus at 20% conversion and ₹18,087 at 25%. Higher prices can reduce conversion; the next page tests that risk.

## 14A Downside and price sensitivity

These are stress scenarios, not predictions or absolute maximum bills. All use 10,000 active users, one Starter purchase per buyer and full redemption. Both operating allowances include the unpaid users; they must be validated against actual usage.

| Monthly buyers | Conversion | Surplus with ₹25k overhead | Surplus with ₹35k overhead |
| --- | --- | --- | --- |
| 1000 | 10% | -₹315 | -₹10,315 |
| 1500 | 15% | ₹12,027 | ₹2,027 |
| 2000 | 20% | ₹24,370 | ₹14,370 |
| 2250 | 22.5% | ₹30,541 | ₹20,541 |
| 2500 | 25% | ₹36,712 | ₹26,712 |

### Higher execution cost stress

| Execution cost per credit | Cost rise | Surplus at 20% buyers and ₹35k overhead |
| --- | --- | --- |
| ₹0.45 | Baseline | ₹14,370 |
| ₹0.60 | 33.3% | ₹2,370 |
| ₹0.675 | 50% | -₹3,630 |

At the same 20% conversion and ₹35,000 overhead, zero surplus occurs near ₹0.630 per consumed credit. This is not permission to raise the ₹0.45 admission ceiling. A breach must pause new affected work, reconcile expense and requalify the route or prospective quote while honoring funded accepted work.

### Pack contribution at full redemption

| Pack at offer price | Maximum execution expense | Contribution after reserves before overhead |
| --- | --- | --- |
| Starter | ₹18.00 | ₹24.68 |
| Growth | ₹58.50 | ₹71.00 |
| Scale | ₹162.00 | ₹162.84 |

Starter is the lowest absolute contribution per buyer under the one-pack assumption, so the all-Starter case is conservative for pack mix. Contribution is not profit: shared overhead, omitted staff or acquisition costs and income tax still matter. The minimum contribution per consumed credit occurs on Scale and must constrain any additional promotion.

At ₹35,000 overhead, an additional ₹10,000 monthly staff or marketing expense leaves only ₹4,370 in the 20% case. Combined adverse events can produce losses. Do not spend anticipated profit or lower deliverable quality to hide a deficit.

## 15 Funding decisions and growth policy

G01  Fund capacity before commitment. Before committing a capacity tier, reserve all accepted delivery obligations, taxes, refundable balances, incident allowances and the committed operating period. Expansion must be funded by collected eligible revenue, not projected registrations. Outstanding customer balances are not available growth capital.

G02  Margin gates. Use 20% monthly conversion, all-Starter offer purchases and ₹35,000 overhead as the approved adverse scenario. It leaves ₹14,370 after reserves. Require at least 15% of defined net receipts after all budgeted costs; seek 20% in normal operation. At 2,000 buyers, overhead must be at most ₹35,141 for 15% and ₹30,399 for 20%. Include omitted costs before testing either gate.

| Monthly shared expense | Buyers to break even after reserves | Buyers for 15% net target |
| --- | --- | --- |
| ₹18,000 | 730 | 1025 |
| ₹25,000 | 1013 | 1423 |
| ₹35,000 | 1418 | 1992 |

G03  Acquisition discipline. Prioritize useful SEO content, product examples and product-led referrals. Fund referral credits only from an approved allowance after a qualifying paid purchase. At 20% conversion, surplus per monthly buyer is about ₹12.18 with ₹25,000 overhead and ₹7.18 with ₹35,000. The latter has only ₹0.07 per buyer of headroom above the 15% gate. Fund acquisition from genuinely available headroom; do not assume repeat purchases or unproven lifetime value.

G04  Additional expense scenarios. Add founder pay, hired staff, marketing, review labor and incremental agent subscriptions before calling the result business profit. An additional ₹10,000 monthly expense reduces the adverse ₹35,000-overhead and 20% case to ₹4,370, below the minimum margin gate. One-time implementation and evaluation budgets require separate approval.

G05  Protect customers when headroom falls. Stop new optional free work, unnecessary refreshes and uncommitted expansion first. Pause new paid admission before exhausting reserved obligations. Preserve already accepted work, access, export and security. Scaling down must respect persistent storage, retention and recovery commitments.

G06  No reliance on exclusions. Do not remove security controls, hide paid-provider bills, call refunds zero cost, assume indefinitely free hosting or treat unused balances as profit to meet a target. If the measured design cannot satisfy quality and economics, revise scope or transparent quotes before launch.

The pilot remains geographically and technically bounded. Wider source coverage, additional portal adapters and new languages each require a cost and quality qualification rather than an open-ended promise funded by current low-price packs.

## 16 Development checklist for foundations

All stages below are pending owner review and implementation authorization. Owners are roles to assign, not staffing commitments. Every completed item needs a dated evidence link and actual scope; routine CI passes do not count as separate product milestones.

### Stage 1 Baseline and measurable contracts

[ ] Product and finance: approve scope, offer and standard prices, campaign dates, validity, service envelopes and margin gates. Map S01 to S05, B01 to B06, P01 to P06 and M01 to M02.

[ ] Engineering: inventory actual deployed routes, wallet liabilities, provider configuration and invoices; distinguish live capability from historical plans.

[ ] Quality lead: freeze supported cohorts, approved reference outputs, rubrics and evaluation data under Q01 and V01 to V03.

[ ] Finance: approve separate development and quality-review budgets, including human reviewers and agent tools.

Exit evidence: approved contracts and decision log, baseline invoice allocation, route inventory and evaluation manifest. No production pricing change.

### Stage 2 Cost and billing foundations

[ ] Backend: implement versioned quotes, the unified ledger, atomic credit and monetary reservations, outbox and reconciliation states under C01 to C08 and A01 to A03.

[ ] Billing: test duplicate payment webhooks, concurrent spends, worker crashes, SDK retries, usable refunds after pass expiry, offer expiry and stale quotes, and unknown provider usage.

[ ] Operations: create cost telemetry and policy-validity controls without logging sensitive content.

Exit evidence: no negative or duplicated balances in adversarial tests; every provider attempt accounted for; accepted work fully reserved; failed work refunded correctly.

### Stage 3 Deterministic processing and reuse

[ ] Backend and data: implement native extraction, contextual skill handling, basic matching, shared job ingestion and revision-aware caches under E01 to E06.

[ ] Frontend: clearly distinguish curated, basic, reused and AI outputs; keep original-resume use independent of paid tailoring.

[ ] Quality: verify unknown terms, negation, numeric claims, stale jobs and cache ownership without substituting generic lower-value outputs.

Exit evidence: deterministic and cache fixtures, confirmed reductions in duplicate work, and no regression in supported relevance or factuality.

### Stage 4 Qualified AI and document routes

[ ] AI and document engineering: evaluate candidate models, compact context and explicit reasoning limits; implement native edit validation and supported-format checks.

[ ] Quality: complete all critical gates and blinded comparisons in Sections 6 to 8, including eight-question completeness and unsupported-input handling.

Exit evidence: each enabled route meets both quality and economic criteria; unqualified routes remain disabled. Savings alone cannot pass this stage.

## 17 Development checklist for release

### Stage 5 Capacity privacy and responsive experience

[ ] Platform: benchmark the stated 10,000-user workload and representative peaks; measure database utilization, Redis commands, worker duration, bandwidth and free-user cost.

[ ] Security: verify scanning, isolation, immutable sources, audit retention, backup restore and device or application approval boundaries.

[ ] Frontend: verify mobile and desktop flows, keyboard access and performance targets under F04 and F05.

[ ] Finance: compare measured usage against ₹18,000, ₹25,000 and ₹35,000 scenarios; include every non-delivery bill exactly once.

Exit evidence: repeatable load report, invoice-backed forecast, privacy tests and accessible review flows. A spreadsheet allowance alone is insufficient.

### Stage 6 Shadow accounting and migration preparation

[ ] Billing: run the proposed tariff and expense policy in shadow mode without changing customer charges. Compare expected charges, actual usage and protected balances.

[ ] Engineering: prepare additive migrations, owner-approved legacy credit mapping, reconciliation totals and a reversible release strategy.

[ ] Product: finalize disclosures, authenticated reference-price evidence, first-launch fallback, campaign expiry, retention and refunds under P01 to P06. Preserve existing rights.

Exit evidence: signed reconciliation, shadow cost and quality results, migration dry run and reviewed rollback instructions.

### Stage 7 Controlled deployment and commercial decision

[ ] Release owner: obtain separate implementation or rollout approval as appropriate; deploy immutable artifacts with feature flags and health checks.

[ ] Quality and operations: start an opted-in canary only after the held-out gates pass; observe quality, corrections, invoices, latency and refunds.

[ ] Product owner: separately approve paid sales, commercial hosting and the dated campaign; test automatic offer expiry and price agreement end to end. Keep final employer submission disabled.

Exit evidence: real permitted end-to-end acceptance, funded rollback, commercial readiness and owner decision. All unaccepted future services remain clearly labeled.

### Stage 8 Ongoing quality and economics

[ ] Operations and finance: reconcile costs daily, review pricing and margins weekly, and investigate discrepancies before increasing limits.

[ ] Quality: sample outputs by route and cohort, review candidate corrections and disable a route on a confirmed critical defect.

[ ] Product: measure monthly purchase conversion, pack mix, redemption, repeat purchases, refunds and acquisition payback before expansion.

Exit evidence: continuing dashboards and review records. Stage 8 is ongoing operation, not a one-time assertion of permanent profitability.

## 18 Rollout rollback and reporting

R01  Release order. After separate authorization: baseline measurements, offline quality gates, shadow accounting, migration rehearsal, opted-in canary, then bounded wider release. Freeze the tested model and policy versions. Do not combine an untested price change, model change and wallet migration into an unobservable cutover.

R02  Shadow and canary. Propose at least seven days of representative shadow accounting and an initial opted-in cohort no larger than 5% of eligible traffic. Require at least 200 successful paid-equivalent operations across supported cohorts before widening; continue longer if coverage or statistical evidence is inadequate. Shadow calls and human review require a funded testing budget.

R03  Rollback obligations. Preserve immutable sources, ledger history, active quotes and accepted work. Use additive schema changes and route flags. A rollback must not double charge, erase credits, revert customer-approved files or retry unknown applications. A prior expensive route can be restored only with sufficient funding.

R04  Stop conditions. Stop the affected optimized route for any confirmed fabrication, privacy leak, original-file corruption or unauthorized application action. Pause new optional execution on budget shortfall, stale supplier policy, sustained failure or a breached quality gate. Investigate before resuming.

### Required operational dashboard

| View | Measures |
| --- | --- |
| Quality | Factual defects, layout failures, incomplete outputs, correction rates and blinded-review scores by route and cohort. |
| Delivery | Attempt count, queue age, p50 and p95 latency, qualified fallback use, unsupported inputs and unknown outcomes. |
| Finance | Gross collections, tax liability, delivery reservations, actual costs, refunds, reserves, overhead and surplus. |
| Acquisition | MAU, monthly purchase conversion, pack mix, paid retention, actual offer redemptions and funded referral or promotion cost. |
| Capacity | API usage, database CU-hours, cache commands, storage, downloads, ingestion load and scanner freshness. |

R05  Evidence reporting. Report completion against the stages in Sections 16 and 17. Include outcome, expected versus actual effort where measured, evidence location and unresolved limits. Do not present repeated CI or regression passes as new major development milestones.

Do not expose candidate data in dashboards or reports. Keep operational alerts actionable and tied to a route, cost period or release. If a provider outage or capacity restriction affects users, explain the limitation without claiming a lower-quality fallback is equivalent.

## 19 Risks and owner review record

| Risk | Required response |
| --- | --- |
| Cheaper route lowers quality | Retain the qualified route or change the upfront quote; never waive critical quality gates. |
| Complex source cannot retain layout | Declare unsupported combinations before charge; preserve original and request suitable source. |
| 10,000-user costs exceed allowance | Use measured capacity and actual invoices; limit unfunded free work or expansion before committing. |
| Sales below monthly assumption | Protect existing obligations; reduce future commitments. A cash reserve funds a loss but does not make it profit. |
| Provider price or FX increase | Refresh the policy, quote prospective work accurately and reserve known scheduled increases. |
| Expired or reduced credit terms harm users | Disclose new terms before purchase and preserve existing balances and accepted operations. |
| Unsubstantiated discount or false urgency | Use first-launch copy until reference evidence exists; enforce a genuine server-side campaign deadline. |
| Submission duplicates or ambiguous result | Keep future execution separately gated; reconcile unknown outcomes and never invent a receipt. |
| Salary or development costs omitted | Add those costs to the model before approving a fully loaded business-profit claim. |

### Decisions requested from the owner

[ ] Approve offer packs ₹59 for 40, ₹179 for 130 and ₹449 for 360 credits; proposed standard prices ₹79, ₹229 and ₹599.

[ ] Approve 30-day pass validity, no automatic renewal and a separate fixed 30-day launch campaign. Set dates before sale; verify P01 before strikethrough pricing.

[ ] Approve the supported service envelopes and higher-cost quote approach, subject to the quality gates.

[ ] Confirm the 20% monthly-purchase baseline, shared expense assumptions and additional salary, acquisition or agent-tool costs.

[ ] Approve the zero-tolerated-quality-degradation policy and funded evaluation work.

[ ] Approve 15% of defined net receipts as the minimum stress gate and 20% normal target; fund obligations before discretionary growth.

[ ] After reviewing this document, separately authorize the implementation stages to begin.

Owner decision: ____________________    Date: ____________________

Approved revisions or conditions: ____________________________________________
________________________________________________________________________

Review approval does not itself activate sales, purchase provider services or authorize employer applications. Those actions retain their separate release and candidate-control requirements.

## 20 Sources and terminology

Public supplier references checked on 27 October 2026. Prices and eligibility can change. Supplier pages support unit-rate and platform facts; HireWiz budget allowances, workload assumptions and profit scenarios are planning calculations, not quotations from suppliers.

[1] Gemini Developer API pricing

Model and token prices, output thinking charges and scheduled changes. A January 2027 increase is listed for 3.6 Flash.

[2] Cloud Run pricing

Compute, memory, duration, concurrency and network billing considerations.

[3] Neon pricing

Launch compute listed at USD 0.106 per CU-hour; actual utilization and storage determine the bill.

[4] Vercel pricing and commercial plan review

Pro starts at USD 20 per month; actual eligibility, seats, tax and usage require review before sale.

[5] Upstash Redis pricing

Pay-as-you-go command pricing and free-tier limits.

[6] Resend pricing

Transactional email capacity and sending limits.

[7] Google Cloud spend cap budgets

Eligible services, enforcement delays, in-flight charges and continuing persistent-resource costs.

[8] Razorpay pricing

Standard fee basis of 2% plus GST; actual methods and contracts can differ.

Advertising sources [9] and [10] are linked in Section 3A and were reviewed on 27 October 2026. The proposed standard prices are not a claim of historical prices.

### Local requirements used for continuity

L1  HireWiz Job Discovery and Application Requirements, docs/requirements/JOB_SEARCH_AND_APPLICATION.md, reviewed worktree hirewiz-pilot-final-status-20261024. Used for pilot boundaries, candidate control and document-preservation requirements.

L2  HireWiz AI call efficiency audit, docs/AI_CALL_EFFICIENCY_AUDIT.md, dated 8 October 2026. Used for deterministic-first workflow definitions and known risks. Historical code observations must be reverified during Stage 1.

### Terminology

MAU: monthly active users. Monthly purchaser: a user buying in that month. Execution ceiling: the admission budget for a bounded paid operation. Shared overhead: costs outside metered paid execution. Reserve: protected funds, not necessarily spent. Operating surplus: remaining modeled funds after defined costs and reserves, before income tax and omitted business costs.

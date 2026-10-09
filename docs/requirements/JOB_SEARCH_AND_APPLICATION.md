# HireWiz Job Discovery and Application Requirements

AI call efficiency and employer portal automation

Version 1.1   |   8 October 2026   |   Development authorized

Prepared for the HireWiz product owner and engineering team. This document specifies the development we recommend, the reasons for the design, integration limitations, delivery stages and evidence needed for acceptance. The product owner authorized development and deployment on 8 October 2026. This revision adds paid search and application accounting, a candidate-selected result count, and performance and responsive interface acceptance requirements. Candidate applications still require their own per-job approval; development authorization is not permission to submit applications for real candidates.

We recommend broad employer-origin discovery, transparent basic matching without mandatory AI, and candidate-approved applications sent to the employer or its authorized ATS. Each application supports the original resume, a reviewed tailored version or a custom upload. Keep the existing GCP deployment approach and current region; defer AWS, Azure, Kubernetes and database relocation.

Direct employer applications are feasible for supported portals. Universal unattended submission and a verified census of all worldwide jobs are not currently feasible promises. We should pursue greater than 95 percent discovery recall within declared, independently measured search scopes, expand those scopes progressively, and report automatic submission coverage separately.

## Contents

1. Product scope and review decisions
2. Current project and development gaps
3. Candidate workflow and functional requirements
4. Coverage and freshness measurement
5. Employer platform connector catalog
6. Discovery expansion and alternatives
7. AI call reduction requirements
8. Resume quality and format preservation
9. System design and data contracts
10. Application execution and recovery
11. Security privacy and candidate control
12. Capacity reliability and cost controls
13. Agent tools plugins and engineering setup
14. Deployment and operating strategy
15. Development stages and verification checklist
16. Evaluation release gates and delivery evidence
17. Risks dependencies and review record
18. Research sources and local evidence

## 1 Product scope and review decisions

### Product promise

The service helps candidates find relevant, recent roles and prepare accurate applications with less repetitive work. Search results explain employer origin, age, match basis and application capability. Candidates choose jobs and control the exact resume and answers disclosed to each employer. Automation executes a reviewed package; it does not make new candidate declarations or silently choose destinations.

An employer career portal includes a company website and a vendor-hosted ATS tenant officially linked or otherwise verified as employer-authorized. Greenhouse or Workday hosting does not turn that tenant into a third-party job board. A recruiter-side candidate record, a partial upload, a lead form or a sourced profile does not necessarily constitute a completed job application.

### Priorities

| Priority | Delivery scope | Reason |
| --- | --- | --- |
| P0 | Candidate facts and controls, origin verification, deterministic discovery and basic fit, immutable files, truthful tailoring, complete forms, safe submission state, receipts | These establish the core service and prevent incorrect or duplicate disclosures |
| P1 | More ATS adapters, browser companion, optional writing and semantic analysis, coverage audits, operational dashboards | These expand usefulness after foundations and permissions exist |
| P2 | Additional languages and sectors, permitted server browser execution, semantic retrieval infrastructure, advanced personalization | Adopt after measured demand and demonstrated benefit |
| Deferred | Cloud migration, Kubernetes, new microservices for every domain, universal browser agent, job-board application routes | These are not initial delivery prerequisites |

### Decisions for product review

Default source policy remains employer-origin only. Licensed search may locate employer URLs, but the source record must be verified before a vacancy enters verified results. A broader licensed job-aggregator locator mode is an optional alternative requiring a separate product decision, commercial rights and origin validation. Board-only jobs cannot satisfy the direct-employer requirement; show the limitation rather than invent an employer route.

Approve an India-first English-language pilot as the recommended starting scope, with a global data model and staged expansion. Choose adult-candidate eligibility, role families, locations, employer populations and recent-job windows before a coverage claim. This recommendation does not restrict the eventual worldwide objective.

Review AI spending limits, displayed product charges, retention periods and initial application batch limits. Suggested pilot controls are 10 selected jobs per reviewed batch, one active send attempt per application and a configurable daily candidate cap. Batch selection is not blanket authorization for unknown future jobs.

Requirement IDs below are stable acceptance references. “Must” denotes a release requirement. P0 safety and ownership requirements cannot be skipped to increase coverage. Named suppliers and scope targets remain proposed until reviewed and contracted.

## 2 Current project and development gaps

The baseline is repository commit d5c2ea8, with the earlier architecture and AI efficiency planning documents. This is a static code and documented configuration assessment, not a measured production traffic study. Current model configuration documents Gemini as the provider, with LLM_MODEL set to gemini-3.6-flash and fallback configuration in code. Reverify actual provider availability and deployed settings during Stage 0; this document does not claim a live successful response from that model. [L1–L4]

| Existing area | Current behavior | Required development |
| --- | --- | --- |
| Frontend and API | Next.js and React on Vercel, FastAPI on Cloud Run | Extend Workspace and preserve the BFF authentication boundary |
| Analysis execution | Private worker and Cloud Tasks already exist | Transactional dispatch, workload isolation, attempt accounting and bounded pools |
| Resume parsing | Native text/contact/date processing plus a logical skills generation call; optional local spaCy name inference | Deterministic extraction, source spans, corrections and optional enrichment |
| Job matching | Combined model analysis, followed by local weighted scoring | New honest basic fit contract and separately labeled enhanced analysis |
| Tailoring | Evidence-based generation with bounded repairs and native layout checks | Better usable edits, sealed preview/export and explicit unsupported-format handling |
| Evidence | User-reviewed section-based facts | Finer claim records, ownership, source provenance and revision invalidation |
| Interview and learning | Generated interview questions; deterministic gaps/resources with optional strategy generation | Curated defaults with useful questions even when personal evidence is sparse |
| Market feeds | Existing third-party market integrations | Separate verified employer-origin pipeline; do not treat those feeds as origin proof |
| Storage and release | Original resume bytes in PostgreSQL; exports rendered on download; startup migrations | Private immutable GCS objects and one controlled migration release job |

Immediate gaps include a commit-before-queue crash window, synchronous expensive upload work, row locks held across tailoring, incomplete nested provider-attempt telemetry and process-local admission controls. These require engineering validation, not assumptions that autoscaling will solve them. Earlier inspection placed core Cloud Run in us-central1 and Neon PostgreSQL in Singapore on AWS; preserve this topology initially while measuring latency and connection limits. [L2]

Two AI reduction defects have priority: the parser fallback uses unsafe substring matching for some skills, and the public bullet optimizer can return an invented fixed achievement on failure. Replace that fallback with the original text and explainable feedback. Dormant helper functions are maintenance opportunities, not evidence of current production call savings. [L3]

## 3 Candidate workflow and functional requirements

### End to end journey

1. Upload a resume. Retain the original bytes, validate the file and display extracted facts with source references.
2. Confirm facts and preferences. Collect target roles, sectors, locations, remote preferences, seniority and explicit work authorization or sponsorship answers where relevant.
3. Search the fresh employer index. Explain matches, uncertain qualifications, dates and supported application mode. Allow broader search and user-selected roles beyond the first recommendation.
4. Select jobs. Each job starts with an independent resume choice: original, tailor or custom upload. Selection does not authorize disclosure.
5. Prepare the application. Fetch current form requirements and optionally draft truthful wording. Request missing facts and declarations from the candidate.
6. Review. Show the exact destination, file, answers, employer policies and requested actions. The candidate may switch back to the original or upload another file after seeing a tailored draft.
7. Approve and execute. Use a supported API or permitted tested browser adapter. Pause for authentication, challenges, new requirements or assessment work.
8. Track. Show a verified receipt, an incomplete application, a human handoff or an unknown outcome accurately. Preserve the reviewed package and attempt history.

### Paid search and auto apply

Search and automatic application execution are separate paid services. Use a dedicated prepaid job service balance rather than free signup analysis units. Premium does not waive job service charges. The initial introductory catalog offered 500 closed-loop service credits for INR 499 through the existing webhook-confirmed Razorpay checkout. The owner subsequently requested exactly three finite prepaid bundles priced against estimated expense and margin; unlimited usage must no longer be offered. Show job-service credits and AI-analysis units separately within each bundle and grant/refund all promised components atomically. These credits cannot be transferred or redeemed as money. Optional resume tailoring keeps separately displayed, versioned analysis-unit pricing; selecting a tailored resume never silently purchases another operation. Preserve accepted historical orders and quotes.

The initial configurable defaults are x = 1 service credit per newly delivered qualifying job and y = 5 service credits per confirmed complete automatic application. These are introductory defaults, not validated production margin floors. Under BI08, new production prices must cover the measured and conservatively bounded expenses of each service and its allocated platform costs. Store immutable price versions on every reservation; configuration changes cannot reprice an approved package or historical charge.

**BI01 Result count and quote.** Must let the candidate select a desired result count from 1 to a configurable launch maximum of 100. Show the search unit price, requested count and maximum reservation before execution. The result count is an upper bound, not a promise to find unavailable vacancies.

**BI02 Search settlement.** Reserve requested count multiplied by x atomically under the owner lock. Commit x only for each fresh, qualifying, unique employer posting newly delivered to that candidate. Release any unused amount. Already-paid saved results, duplicate postings, empty results, unavailable sources and safely failed searches do not incur another search charge. A new price or search identifier cannot bypass deduplication of paid delivery.

**BI03 Separate execution charge.** Show y per supported job before approval and reserve it before enqueueing execution. Commit only after the connector proves a complete application receipt. Uploading a file, saving a draft, preparing answers, a manual handoff or receiving an unverified 2xx response is not a paid successful auto-application.

**BI04 Failure and uncertainty.** Release reserved execution credits on proven pre-send failure, withdrawal before launch, unsupported submission or cancellation that wins before disclosure/send. A possible-sent timeout stays unknown with an explicit temporary credit hold, no blind repeat send, and a reconciliation task. Resolve the hold through provider proof or a documented support action with an audit record. Never turn an unknown outcome into a success just to settle billing.

**BI05 Paid entitlements.** Grant service credits only from verified captured payment and the immutable order entitlement snapshot. Duplicate captures, late webhooks, concurrent purchases and refunds must not duplicate or mint entitlements. Process partial refunds proportionally and record spent refunded credits as account debt rather than granting free replacement use. Keep existing financial retention and account-unlinking rules.

**BI06 Ledger and controls.** Provide balance, held amount, operation, unit price, quantity, settled cost and refund/release history separately for search and application. Enforce insufficient-balance errors before paid work. Batch approvals enumerate the exact jobs and total costs; enforce per-candidate daily limits and one active possible send per opening.

**BI07 Pricing transparency.** Display paid service credits separately from analysis units and Premium. Show optional tailoring charges separately, allow original or custom resumes without tailoring, and keep price quotations immutable until their stated expiry. Do not advertise unlimited auto-apply or paid search with Premium.

**BI08 Expense-based pricing and margin protection.** Before activating a new catalog or service-price version, validate every purchasable pack and allowed discount against a versioned expense policy. Include provider attempts/fallbacks, failed or unknown work, document compilation/scanning, cloud compute/storage/database/queue/egress, source licenses, payment fees and nonrecoverable tax, refunds/chargebacks, support, currency conversion, fixed platform allocation and a contingency allowance. Use the lowest net revenue per credit among all allowed packs and promotions, integer/Decimal arithmetic with upward cost rounding, and a positive target contribution margin after costs. Enforce bounded expenses before execution and preserve unresolved cost liabilities. Missing, stale or incompatible cost evidence cannot silently become zero cost. Existing orders, entitlements and accepted quotes retain their exact promised terms; new prices need new quotes. Optional AI tailoring remains separately quoted, and manual handoff still has no automatic-application fee. Reconcile actual vendor invoices and net settlements with forecasts monthly, evaluate low-volume break-even and alert or restrict new costly operations before their budget is exceeded. Positive unit margin does not prove whole-business profitability without enough paying usage to recover fixed expenses. See [the staged expense-pricing policy](../delivery/COST_PRICING_POLICY_2026-10-09.md).

For example, requesting 20 new results reserves at most 20 service credits. If only 14 qualifying new jobs are delivered, charge 14 and release 6. Approving 10 supported applications reserves 50 separately. If 8 are confirmed and 2 fail safely before sending, charge 40 and release 10. The total paid service cost is 54 credits, with any explicitly requested resume tailoring itemized separately.

### Functional requirement register

**FR01 Candidate facts.** Must support correction and approval of extracted name, contact details, employment, projects, education, skills and achievements. Store claim-level source spans, confidence and extraction provenance. Keep locations out of candidate names and preserve employers, dates, links and quantitative claims.

**FR02 Preferences.** Must version explicit role, country, city, language, salary and currency, employment type, remote eligibility, relocation and sponsorship preferences. Required choices remain unknown until answered. Do not infer citizenship, demographic traits, disability, criminal history or authorization from a resume.

**FR03 Search.** Must support keyword and taxonomy filters, country and remote restrictions, date windows, employer exclusions, pagination and stable sorting. Query results use a persisted search snapshot or cursor so background refreshes do not duplicate or skip pages. Display search scope and source health.

**FR04 Basic fit.** Must retrieve and rank without an external model request. Explain matched skills, related experience, missing evidence and preference conflicts. Hard exclusions apply only to explicit, confirmed requirements. A missing resume mention is missing evidence, not proof of inability. Show uncertainty and permit candidate overrides.

**FR05 Resume choice.** Must store original, tailored or custom choice separately for every job, before and after tailoring. Generation cannot replace the attachment automatically. A source snapshot remains labeled unchanged; a rejected or empty edit set is not a tailored success.

**FR06 Preparation.** Must load current required and conditional questions, allowed answers, uploads, constraints, consent text and assessments. Reuse confirmed answers only when the question meaning, employer scope and validity still match. Unknown questions create a task for the user.

**FR07 Review and approval.** Must show exact employer and tenant, job and form versions, every answer, consent choices, exact file preview and allowed actions. Approval binds an immutable per-job package. Batch approval must enumerate each package and show jobs that need attention.

**FR08 Execution and tracking.** Must execute only approved, valid, supported packages, record attempts and preserve provider receipts. Distinguish confirmed, user-reported, incomplete, rejected, safely failed, unknown and cancelled outcomes. Do not mark success solely from a 2xx response or a candidate creation ID.

**FR09 Candidate controls.** Must support pause, cancel, revoke approval, change resume, exclude employers, review history and delete account data. Queued work checks current controls before any disclosure. In-flight cancellation is best effort and clearly reported.

**FR10 Interface quality.** Must provide responsive, keyboard-accessible forms, readable previews, pending-action guards, resumable progress and actionable failure messages. Use status text with icons; avoid color-only states. Respect reduced-motion settings and prevent animation from delaying a critical application action.

## 4 Coverage and freshness measurement

### What greater than 95 percent means

No verified global census establishes every currently available job. Public, private, referral-only, board-only and unindexed vacancies have different observability. ATS brand counts, vendor customer counts and search engine result counts cannot prove worldwide job recall. We should record the worldwide objective as an expansion ambition and use auditable scope-level release criteria.

**CV01 Scope definition.** Before a measurement, freeze countries, languages, role families, employer populations, public-posting eligibility, candidate-query criteria and observation window. Public vacancies on unsupported career portals remain in the declared eligible reference denominator; they do not disappear because a connector is missing.

**CV02 Independent reference.** Build a stratified employer sample independently of our existing source registry. Cover employer size, sector, geography, ATS family and custom portals. Enumerate vacancies through authoritative feeds plus independent human review. Include holdout employers and independently collected candidate searches to detect unknown-employer and query blind spots.

**CV03 Posting recall.** Measure reference vacancies captured with a verified canonical employer destination divided by all eligible reference vacancies at the same observation time. Canonicalization failures, truncation and unsupported-source misses count as misses. Track withdrawn vacancies and time mismatches separately; reconcile audit dates so expired postings do not distort the denominator.

**CV04 Claims gate.** Require a reported 95 percent confidence interval lower bound above 95 percent before claiming greater than 95 percent recall for a scope. Report sample sizes, sampling weights, countries, sectors and languages, with methods appropriate for clustered employer sampling. Aggregate success cannot hide a failed critical launch stratum. Publish known private or inaccessible classes separately and state the scope beside any public claim.

**CV05 Query and ranking recall.** A posting may be ingested yet absent from a candidate search. Audit search/filter recall against independent relevant-job judgments, then measure top-K usefulness separately. Preserve a view-all route. Do not use only clicked or previously ingested jobs as the relevant reference set.

| Measure | Denominator | Required reporting |
| --- | --- | --- |
| Employer discovery | Eligible independently sampled employers | Verified origins found, unknown employers and unsupported portals |
| Posting discovery recall | Eligible reference vacancies | Captured verified openings, misses, confidence and strata |
| Query retrieval recall | Reference jobs relevant to a fixed candidate search | Returned jobs and false exclusions before ranking |
| Preparation coverage | Selected eligible postings | Complete schema and reviewed package availability |
| Automatic submission availability | Selected eligible postings | Permissioned API or tested permitted automatic route |
| Confirmed execution success | Every attempted approved automatic submission | Confirmed complete receipts, failures, unknown and partial outcomes |

**CV06 Freshness.** Preserve original publication, republication, first seen, last changed, last successful origin check and closure timestamps separately. An updated_at value does not necessarily represent a newly posted job. Never label an unknown publication date as “posted today.” Allow “first seen” filtering as a distinct choice.

**CV07 Scan integrity.** Use full, successfully completed scans and documented explicit status to detect closure. A partial page, 429, timeout or provider outage cannot mass-close jobs. Persist pagination checkpoints, scan generation, counts and content hashes. Recheck vacancy availability before preparation and sending; show stale source health during an outage.

Recommended initial objectives are 95 percent of active postings in the frozen covered-source scope checked within six hours, and expiry corrections within one successful scan cycle. Failed or unhealthy sources remain in the headline denominator and accrue stale/unavailable counts; do not improve the metric by removing outages. Report healthy-source freshness separately as a diagnostic. Provider-specific exceptions must be declared beside the overall result. These are proposed targets, not demonstrated current performance.

## 5 Employer platform connector catalog

This catalog is a researched expansion inventory, not an exhaustive census of every vendor or proof that a connector has been built. Each employer tenant still requires origin verification, permission classification, schema tests and regional configuration. Priority follows measured relevant-job yield, source quality, access feasibility and maintenance cost; do not invent market-share percentages.

Public read means documented published-job access. Permissioned means an employer or approved partner credential or enablement is needed. A visible portal means a candidate can use its career flow; automated behavior still needs a verified permitted adapter. Read credentials cannot be reused as write permission.

### Initial public feed and posting connectors

| Platform | Discovery route | Direct application boundary |
| --- | --- | --- |
| Greenhouse | Public employer Job Board GET API with job details and form questions | POST requires employer Job Board API key; validate full required form independently [S01] |
| Lever | Public company-scoped Postings API; separate global and EU hosts | POST requires employer key. Public posting API omits custom questions; use hosted flow unless parity proven [S02] |
| Ashby | Public employer Job Postings API; exclude unlisted postings from general search | Permissioned application form submission; inspect blocked messages and forbid unpublished-job submission [S03–S05] |
| SmartRecruiters | Public company Posting API | Protected Application API with candidate_applications_manage authorization and current configuration [S06–S07] |
| Workable | Documented public published-job endpoints by company subdomain | Employer w_candidates access; fetch application form and explicitly create an applied candidate [S08–S10] |
| Personio | Employer XML open-position feed including locale variants | Employer Recruiting API token and company ID; file upload and attributes require full schema [S11–S12] |
| Pinpoint | Public locale-specific postings.json feed | Employer API key; required screening answers not enforced by API [S13–S14] |
| Recruitee | Employer XML/widgets plus Careers Site API | Careers token transition enforcement 10 February 2027; validate required questions despite permissive endpoint [S15–S16] |

Ashby partner feeds are employer opt-in, not every Ashby customer. Lever public posting applications cannot be assumed equivalent to authenticated recruiter APIs. Pinpoint postings.json replaces deprecated jobs.json and preserves multiple postings for one job. Keep locale/posting IDs distinct from underlying requisition identities. [S02, S04, S13]

### Additional employer ATS connectors

| Platform | Discovery route | Direct application boundary |
| --- | --- | --- |
| Teamtailor | Employer career page or API with key, even for Public data scope | Admin scope for applications; API answers lack file and video parity [S17] |
| BambooHR | Employer career page; documented ATS job API is authenticated | Candidate creation requires ATS access and write permission; custom-question parity is unverified [S18–S19] |
| BreezyHR | Employer published career positions; documented API uses account authorization | Applied mode differs from sourced. Missing fields can return 202 and require email completion [S20–S21] |
| JazzHR | Employer career pages; partner feed may locate openings | Partner Apply API credentials and per-job questions; provider file limits apply [S22] |
| Comeet or Spark Hire Recruit | Careers API with employer UID and token | Verified hosted or embedded application widget; no unrestricted REST submission established [S23–S24] |
| Zoho Recruit | Employer career pages and published webforms | Recruiter Candidate insert is not a complete application; Create Submission refers to client or hiring-manager submission [S25–S26] |
| Freshteam | Legacy tenant career pages and authenticated API | Sunset compatibility only; renewals stopped 7 June 2026, remaining access depends on tenant expiry [S27] |
| Keka | Employer career portals; authenticated Hire jobs API | API can include confidential and archived jobs; filter published external listings. External submission contract needs validation [S28] |
| RippleHire | Employer career pages; vendor integration and partnership capability | Employer or vendor agreement and demonstrated candidate-application parity needed [S29] |
| Darwinbox | Employer recruitment portals | Product verified, open third-party application contract not established; research and permitted portal pilot [S30] |
| TurboHire | Employer recruiting and career portal family | Product verified, general external application API not established; research and permitted portal pilot [S31] |

### Enterprise career and integration platforms

| Platform | Discovery route | Direct application boundary |
| --- | --- | --- |
| Workday | Employer external career sites and authorized tenant integrations | Integration security required; Put Candidate is not universal candidate auto-apply. Anonymous CXS contract not established [S50] |
| Oracle Recruiting Cloud | Employer portal or provisioned Job Distribution | Employer-provisioned Direct Apply partner integration; internal CE endpoints are not a supported external contract [S51–S52] |
| Oracle Taleo Enterprise | Configured employer career sections and customer job feeds | Customer integration or hosted flow; guest/account settings vary. Older guides require current confirmation [S53] |
| SAP SuccessFactors | Employer career sites or authorized recruiting integration | Permissioned JobApplication OData; draft and applied states differ. Never bypass required-field validation [S54–S55] |
| iCIMS | Approved OAuth XML feed with employer/career-site opt-outs | Partner Apply Framework; applicant association is not proof of completed screening and consent [S56–S57] |
| Jobvite and Employ | Paid keyed employer job feed; external published requisitions only | Feed guide requires hosted Jobvite apply page. Employ products need separate adapters [S58] |
| Avature | Employer career sites and customer-configured APIs | Fields and workflows are tenant-specific; no general anonymous submission contract established [S59] |
| BrassRing and Talent Gateway | Verified employer gateways; historical integration documents exist | Current vendor and contracted interface need confirmation; historical IBM docs do not prove current public apply access [S60] |
| ADP Recruiting Management | Employer portal or product-specific authorized integration | Application create/update requires ADP TPC and enabled vendor/client operations; do not inherit this for Workforce Now [S61] |
| UKG Pro and UKG Ready | Employer career sites and specific partner integrations | General candidate submission contract unverified; product-specific permission and pilot needed [S62] |
| Dayforce | Employer-enabled job and questionnaire feeds; disabled by default | Authorized Candidate Sourcing with a job ID can create an application; UUID processing status must confirm completion [S63] |

Enterprise credentials can expose internal vacancies; never index those merely because a credential can read them. The iCIMS standard feed updates three times daily, so a uniform six-hour feed objective may require a different contracted route or an explicitly disclosed provider-specific freshness target. Jobvite's older guide states a daily quota that must be rechecked commercially. SAP's old Recruiting Management XML feed is deprecated; do not adopt it as a new long-term contract. [S55–S58]

### Connector onboarding contract

**CN01 Registry.** Every connector stores provider family, region, employer, verified tenant and destination, source method, permitted operations, credentials reference, permission evidence, endpoint limits, schema and adapter versions, capability status, last successful test and owner. Credentials live in Secret Manager; registry records contain references.

**CN02 Capability granularity.** Separate discover, fetch details, fetch form, fill, upload, submit and reconcile. Classify API, assisted browser, automatic browser, preparation-only and unsupported independently. Reevaluate after platform changes or permission expiry.

**CN03 Admission tests.** Require complete pagination, date handling, closure, duplicate handling, field parity, conditional branches, current consent, file limits, confirmation proof and rate-limit behavior. An employer sandbox or expressly authorized test environment is required for write validation; no fake production applications.

**CN04 Provider edge cases.** Greenhouse and several other APIs may accept incomplete fields; Breezy can return an incomplete 202; Ashby can report a blocked form; recruiter APIs may default to sourced. Treat these as domain outcomes rather than generic HTTP success. Do not expose private or unlisted openings in general search.

## 6 Discovery expansion and alternatives

### Origin acquisition and long tail coverage

Use public ATS feeds and verified employer career pages as the backbone. Build an employer registry from candidate-provided URLs, verified company domains, official directories, licensed web search and employer opt-in onboarding. NSE lists can seed Indian listed companies, but do not represent private employers or prove a career URL. Registry acquisition and vacancy extraction are separate tasks.

Custom portals need a hierarchy: documented feed or API, JobPosting JSON-LD, sitemap and HTML parser, then a rendered-page parser where permitted and necessary. Structured markup is optional; do not discard real vacancies that lack it. A directApply property describes a simpler application path, not an automation license. Search engine indexing APIs cannot enumerate other companies’ jobs. [S32–S34]

Maintain adapter candidates for PeopleStrong, TalentRecruit, Recruit CRM, Ceipal, Oorwin, Recruiterflow, Bullhorn, SAP legacy career pages, Oracle Taleo variants, Phenom, Eightfold and bespoke portals. These are discovery and partnership research backlog items, not verified API capabilities. Identify the actual underlying ATS and official employer relationship before selecting an integration; recruitment agencies and staffing intermediaries must be classified separately.

### Government and regional origin sources

| Source | Scope and access | Application plan |
| --- | --- | --- |
| USAJOBS | Official US federal Search API, key required, query/page limits | Follow ApplyURI to the actual authorized agency flow; no public submit API established [S35] |
| India NCS | Government service includes direct employer and partner jobs | Keep direct NCS employer postings distinct from partner locators; verify destination and portal path [S36] |
| UK Civil Service Jobs | Official government recruitment portal | Account, form and real-person challenges can require handoff; no general submit API established [S37] |
| Canada GC Jobs | Federal vacancy and application portal | Candidate account and screening answers; preserve exact requirements and permit portal text resume conversion review [S38] |
| EU Careers institutions | Participating institution vacancies and deadlines | Follow the specific institution notice; no universal application route [S39] |

These sources add public-sector coverage; they do not cover entire national labor markets. Employer-direct opportunities discovered on government services remain labeled by provenance. For every source, retain original eligibility, citizenship, language, examination and deadline constraints without inferring candidate answers.

### Licensed alternatives requiring review

Brave Web Search is the recommended current search-locator candidate. Its bounded ranked results can help find employers and URLs, but cannot establish complete worldwide recall. Google Custom Search JSON API is unavailable to new customers and requires existing users to transition by 1 January 2027. Bing Search APIs retired in August 2025. Neither should underpin a new discovery system. [S40–S42]

Adzuna is a documented licensed locator option, but its redirect URL and truncated descriptions do not guarantee an employer canonical link. The existing Jooble and TheirStack integrations may be evaluated under the same rights and provenance checks, without assuming they are approved for this new service. Do not automatically redistribute, retain or crawl results beyond contract rights. [S43]

LinkedIn, Indeed, Naukri, Foundit, Glassdoor, ZipRecruiter, SEEK and JobStreet, StepStone, Reed, Totaljobs, Welcome to the Jungle, Wellfound, Monster, CareerBuilder, Bayt, GulfTalent, Dice and regional boards belong in a commercial locator evaluation backlog. This is an inventory of possible channels, not a claim of open search APIs or accessible partnerships. LinkedIn Job Posting and Indeed Job Sync are employer/ATS publishing integrations, not universal candidate job-search feeds. No job-board application route is included. [S44–S45]

The best practical alternative to universal automation is broad verified discovery plus layered execution: authorized API submission, permitted tested browser execution, user-assisted filling, then an employer-portal handoff with prepared answers and exact resume. If a vacancy exists only on a board, report it as incompatible with the origin policy or keep it outside verified results. Do not manufacture a direct portal route.

## 7 AI call reduction requirements

### Processing categories

Deterministic processing uses parsing, dictionaries, rules, SQL, templates and state transitions without trained-model inference. Local spaCy, learned OCR and embeddings are still AI inference, even when no external LLM call occurs. Reusing saved generated content avoids a new call but remains AI-derived. These categories must remain visible in provenance and cost reports.

| Workflow | Default without external AI calls | Optional AI value |
| --- | --- | --- |
| Upload and facts | Native extraction, bounded taxonomy, date arithmetic, candidate correction | Ambiguous structure or novel terminology |
| Employer trust and discovery | Registry, documented feeds, source parsers and link verification | Suggestions only; never permission or trust authority |
| Normalize and deduplicate | Provider mappings, locale dictionaries, IDs, canonical URLs and hashes | Ambiguous narrative enrichment or duplicate suggestions |
| Retrieve and basic fit | SQL full text, taxonomy aliases, explicit weights and evidence coverage | Nuanced transferable experience after evaluation |
| Resume selection and export | Immutable bytes and native validation | Tailored wording with approved facts |
| Forms and answers | Known field adapters and candidate-confirmed values | Reviewed motivation or project answer drafting |
| Submit and reconcile | Deterministic authorization, adapters and valid receipts | No generative authority required |
| Interview and learning | Curated role questions, evidence scaffolds and resource catalogs | New scenarios and personal coaching |
| Career memory and direct facts | Structured CRUD and supported stored-field responses | Open-ended synthesis |
| Billing and security | Cryptography, ledgers, quotas, ACLs and audit records | Optional redacted operational summaries only |

**AI01 Independent core path.** Must complete fresh upload, confirmation, discovery, basic fit, original/custom selection, user-written required answers, supported execution and receipt tracking with external LLM access disabled and empty caches. This test must not rely on earlier AI-derived facts. Run a separate warm-cache test and label AI-derived reuse accurately.

**AI02 Extraction quality.** Replace substring skill matching with bounded aliases, short-token context, source spans, negation handling and nontechnical vocabulary. Test Java versus JavaScript, R versus ordinary words, overlapping employment dates, abbreviations and ambiguous dates. Unknown facts remain reviewable.

**AI03 Honest analysis modes.** Basic fit must not fabricate the existing model’s eight dimensions. Publish a new schema with explainable strengths, evidence gaps, explicit preferences and uncertainty. Enhanced analysis is optional, versioned and evaluated against the basic path on independent candidate/job examples.

**AI04 Generation budgets.** Persist per-operation attempt and cost ceilings before execution. Every provider attempt, fallback, repair and worker retry consumes the same operation budget. Recommended starting cap is three total provider attempts for one writing operation, then a useful partial result or actionable failure. Reevaluate per feature rather than nesting three attempts at every layer.

**AI05 Semantic reuse.** Use dependency fingerprints and coalesce concurrent equivalent work. Private resume results include owner, file hash, parser, taxonomy, corrections, mode and any learned-model/enrichment versions. Enriched results additionally include prompt and provider/model policy versions. Public job normalization uses tenant/posting/content/parser versions and separate enrichment dependencies. Changed relevant inputs invalidate only affected layers.

**AI06 Telemetry.** Record actual attempted and successful provider/model, returned tokens, estimates when unavailable, latency, repair reason, cache hit, coalescing and cost estimate. Measure calls per useful completed result before claiming savings. Product analysis units and non-AI infrastructure costs are separate from model-call counts.

**AI07 Safe fallbacks.** Return the original text and local guidance when rewriting fails. Never invent achievement metrics, roles, qualifications or future project results. Unsupported JSON cannot become an unvalidated ready-to-send answer. Generation errors must not silently trigger new unbounded work.

**AI08 No incidental generation.** Navigation, tabs, polling, approval, metadata refresh, unchanged preview/download and receipt reconciliation must not invoke generation. An explicit new writing variant is a distinct budgeted action. Provider prompt caching still makes a request; do not count it as zero calls.

**AI09 Sensitive input exclusion.** Government identifiers, passwords, session tokens, demographic, health and criminal-history answers, and candidate consent decisions must not enter external-model prompts. Remove sensitive resume fields before optional generation. Protected attributes do not participate in basic or enhanced fit. Explicit location and work-authorization constraints use reviewed deterministic eligibility rules, with unresolved cases shown for candidate review. Confirmed sensitive form answers flow only through deterministic, authorized filling and required retention controls.

Default interview preparation should contain eight curated role-relevant questions across skills, experience, behavioral topics and role scenarios when the catalog supports the role. Sparse evidence changes the personal guidance and evidence state, not the availability of general role practice. Never draft personal achievements without approved facts. Curated questions must be labeled curated, and optional generated questions labeled generated.

## 8 Resume quality and format preservation

**RS01 Original fidelity.** Preserve uploaded bytes and the original downloadable file permanently within declared retention. Parsing is a data extraction view, not a replacement resume. Keep source section order, header relationships, bullets, typography, links, dates and achievements available for review.

**RS02 Native edit strategies.** For DOCX, patch specific source runs and paragraphs while retaining styles, numbering, tables and links. For LaTeX source, patch known text spans while preserving macros and layout; compilation of multi-file sources requires a supported application sandbox and explicit dependency handling. For text-based PDF, make conservative supported edits using layout geometry and font availability, with round-trip rendering tests. PDF is a finished layout and cannot always support arbitrary replacement length.

For application LaTeX processing, use a pinned minimal TeX Live image or an evaluated equivalent, with shell escape disabled, no network, an allowlisted dependency set, bounded CPU/memory/time, a disposable restricted filesystem and path-safe archive extraction. Reject unsupported dependencies instead of executing user commands or downloading arbitrary packages. This production processor is separate from Codex's built-in standalone document compiler.

**RS03 Unsupported cases.** Scanned, encrypted, malformed, complex multi-column or non-editable files receive explicit capability status. Optional OCR is separately classified as learned processing where applicable. Offer unchanged original/custom upload, an editable source request, candidate editing or a separately approved clean template. Never silently rebuild the resume in a generic style and claim preservation.

**RS04 Truthful edits.** Tailoring can improve clarity, ordering of supported emphasis and role terminology. It must not fabricate skill possession, employment, dates, seniority, education, accomplishments or impact. Link each substantive proposed claim to approved evidence and retain before/after text. Keyword insertion alone does not prove ATS suitability.

**RS05 Layout validation.** Check overflow, collisions, font substitution, missing glyphs, page count, line wrapping, bullet structure, hyperlinks and text reading order. Render before and after and compare supported changed regions. Keep validated meaningful edits when other edits fail; disclose partial tailoring. A zero-edit version is an unchanged snapshot.

**RS06 Parsing suitability.** Test extractable text, standard section recognition, required contact fields, upload type/size, missing text and confusing reading order. If the original layout has poor machine readability, explain the tradeoff and offer a reviewed alternate layout. Do not guarantee parsing by every employer ATS or selection probability.

**RS07 Artifact sealing.** After successful review preparation, store immutable exact file bytes, MIME type, size, renderer/validator versions and SHA-256. Preview, download and application upload must use that same artifact. Regeneration after approval creates a new package requiring review, even if wording is intended to match.

**RS08 Failure experience.** Replace generic repeated “try again” loops with a reason and useful next step: unsupported font, no fitting replacements, invalid source span, file too large or editable source needed. Make source snapshots usable and avoid charging a tailoring success for no validated edits according to the reviewed billing policy.

## 9 System design and data contracts

### Proposed topology

Candidate browser → Next.js UI and Vercel BFF → Cloud Run core API → PostgreSQL authority.

Core API → private GCS artifacts and disposable Redis cache. PostgreSQL outbox → dispatcher → Cloud Tasks workload queues → private ingestion, document, optional AI and authorized submission workers. Cloud Scheduler coordinates due sources and maintenance through committed work records. A candidate browser companion communicates with the core API and verified employer portals.

Retain a modular backend in one repository with clear service interfaces. Deploy worker roles separately where resource, dependency or credential isolation matters. The document processor cannot access submission credentials. Introduce additional services only for measured contention, ownership or security boundaries; this avoids distributed transactions for approvals and candidate facts.

**SD01 Durable authority.** PostgreSQL owns candidate facts, employer registry, immutable posting/form versions, application intent, reviewed packages, approval, attempts, receipts, usage and audit events. Redis cannot establish consent, financial state or whether an application was sent.

**SD02 Transactional dispatch.** Commit domain state and outbox intent atomically. A dispatcher creates Cloud Tasks and records progress; duplicate dispatch is expected. Workers claim using compare-and-set transitions, leases and fencing. A sweeper repairs abandoned safe work. Cloud Tasks does not supply our complete failed-work workflow; maintain a durable failed-work table with retry classification. [S46]

**SD03 Objects.** Use private GCS buckets for quarantined uploads, original files, sealed exports and restricted evidence. Store owner, hash and object generation in PostgreSQL. Use short-lived authorized access, antivirus or equivalent scan, object size limits and deletion outbox. Clean abandoned uploads and retain approved artifacts by immutable object identity. [S47]

**SD04 Versioned domains.** Use UUID internal identities plus provider identifiers. Preserve employer, source and requisition identity separately from locale postings. Store form schema and question identifiers, conditional branches, allowed values and consent text versions. A text hash alone cannot establish the same job or question.

### Core records

| Record | Key fields and integrity rules |
| --- | --- |
| Employer and source | Official domain, tenant, region, trust evidence, operations, grant reference, health and scan cursor |
| Posting and version | Provider ID, requisition ID, employer, canonical URL, locale, locations, dates, status, original content hash |
| Candidate fact and evidence | Owner, source object/span, confirmed value, provenance, approval and revocation revision |
| Resume artifact | Owner, immutable object version, SHA-256, MIME, size, source lineage, validation and rendering versions |
| Form schema and answer | Stable provider field ID, type, branch rules, constraints, employer scope, question version and answer provenance |
| Application package | Candidate, opening, recipient tenant, exact file, answers, consents, version bindings and canonical package hash |
| Approval and execution | Allowed actions, expiry, revocation, device or executor, execution epoch, attempt state and proof |
| Receipt and accounting | Provider reference, authenticated proof, timestamp, receipt status, usage transition and audit identity |

Use a partial unique constraint for one active application intent per candidate and canonical opening, with an explicit controlled reapplication policy. The same provider ID across different tenants is not globally unique. Enforce owner checks for every nested evidence, artifact, package and receipt relationship, not only the outer workspace route.

### Proposed API surface

Keep current authentication and BFF contracts. Add versioned resources for searches, preferences, source capability, preparation and approvals. Suggested endpoints include POST /v1/job-searches, GET /v1/job-searches/{id}/results, POST /v1/applications/prepare, PATCH /v1/applications/{id}/selection, GET /v1/applications/{id}/package, POST /v1/applications/{id}/approve, POST /v1/applications/{id}/execute, POST /v1/applications/{id}/cancel and GET /v1/applications/{id}/events. Workers receive opaque task identities, not resume text or credentials in queue payloads.

**SD05 Contract semantics.** Mutations accept idempotency keys and expected resource versions. Return 202 plus operation status for asynchronous work, 409 for stale review/version conflicts, 422 for missing or unsupported input, 429 with retry guidance for admission and a stable domain error code for connector outcomes. Actual approved execution still needs live checks; an API idempotency key is not a content reuse key or employer deduplication guarantee.

## 10 Application execution and recovery

### Supported modes

**EX01 Authorized API.** Prefer documented candidate application APIs with employer or partner authorization, current form parity and reliable proof. A recruiter candidate insertion endpoint needs demonstrated applied status and all required workflow steps before it qualifies.

**EX02 Candidate browser companion.** Use a Chrome Manifest V3 extension or equivalent explicitly scoped client for permitted tested career adapters. Candidate passwords and browser cookies remain local. Obtain narrow origin permissions; bind commands to candidate, device, exact employer tenant, package, action and expiry. Allow account login, CAPTCHA, MFA and assessments to remain human steps. Persist checkpoints because extension service workers can terminate. [S48]

Browser and API executors must obtain the same durable final launch permit and current independent recovery epoch immediately before sending. An offline cached command cannot authorize submission and must fail closed. No automatic browser pilot bypasses the Stage 5 launch, uncertainty, cancellation and receipt gates.

**EX03 Server browser.** Defer unattended server browsers until a portal permits them and isolation, session consent and adapter economics are proven. Use tenant-isolated short-lived sessions, controlled egress, credential rules and destruction. Do not use stealth bypasses, CAPTCHA-solving services or fake accounts as coverage strategies.

**EX04 Handoff.** Unsupported forms still receive the reviewed resume, copyable answers, progress and employer link. Label fill-only and candidate-submit modes honestly. A human final click is not an automatic completed submission.

### State and authorization rules

The proposed state machine is DRAFT → PREPARING → NEEDS_INPUT or READY → APPROVED → QUEUED → FILLING → FILLED → SUBMITTING → CONFIRMED. WAITING_USER, REJECTED, FAILED_SAFE, UNKNOWN and CANCELLED are explicit branches. Each transition records actor, version, reason and timestamp. Preparation can resume without granting send permission.

**EX05 Approval binding.** Canonicalize and hash the package’s employer/tenant, job, form, required questions, answers, consents, exact artifact and actions. Candidate changes, revoked evidence, material job changes, changed questions or changed employer policies invalidate affected approval. Recheck permission, account status, cancellation and bindings before both filling/uploading and sending: portal autosave can disclose personal data before the final button.

**EX06 Send boundary.** Acquire the final launch permit with an atomic compare-and-set check and persist SUBMITTING before the external request. Keep network work outside a long database transaction. A local transaction cannot guarantee exactly-once behavior at an external employer. Use provider idempotency only when explicitly documented and correctly scoped.

**EX07 Unknown outcomes.** A timeout, dropped connection, crash after send or expired lease may mean the employer accepted the application. Mark UNKNOWN and reconcile using supported receipt/status lookup. Never automatically retry a possible send merely because a worker or task retries, or because a 5xx response occurred. Retry only after a proven no-send result or a documented provider idempotency contract.

**EX08 Cancellation race.** Serialize cancel and final launch decisions against the same current application version. If cancellation wins, prevent subsequent disclosure/send. Data already disclosed through earlier filling or autosave may remain with the employer. If the send already launched, show that withdrawal is not guaranteed. A valid late receipt can resolve UNKNOWN or an in-flight cancellation. Stale executors cannot launch a new send; authenticate and validate late receipt evidence through a separate reconciliation path.

**EX09 Confirmation evidence.** Require a provider application reference and correct completed status or other tested portal receipt proof bound to the candidate, job and attempt. Distinguish registration, sourced profile, draft, partial application, uploaded file and full submission. User reports remain labeled user-reported until independently verified.

## 11 Security privacy and candidate control

**SE01 Untrusted sources.** Treat job descriptions, resumes and portal text as data. Ignore embedded instructions that ask a model or agent to change recipients, disclose secrets or authorize actions. AI receives bounded source material and cannot issue application commands. Validate all generated outputs against domain rules.

**SE02 Network safety.** Protect URL fetches against SSRF, private/reserved networks, DNS rebinding and redirect escapes. Verify employer tenant identity, not just a shared ATS hostname. Limit content size, decompression, timeouts, redirects and renderer egress. Sanitize descriptions before frontend display.

**SE03 Credentials.** Use workload identities and least privilege for Cloud Run, Tasks, storage and deployments. Store employer secrets in Secret Manager with rotation, expiry and operation scope. Keep model credentials away from browser/document workers when unused. Never paste credentials into agent prompts, CI logs or job payloads. [S49]

**SE04 Data minimization.** Store only candidate-authorized application information. Avoid retaining full sensitive form screenshots by default. Redact logs and traces; use hashes and opaque IDs for debugging. Do not infer protected characteristics. Consent to one employer’s data processing or talent pool cannot authorize another employer.

**SE05 Retention and deletion.** Publish separate candidate originals, drafts, receipts, source snapshots, telemetry and backup retention policies before launch. Account deletion immediately revokes new disclosures and queued work, then uses durable deletion tasks. Explain backup expiry and employer-held copies that HireWiz cannot delete itself. Legal and regional privacy obligations require jurisdiction-specific review; do not claim compliance merely from this design.

**SE06 Access and proof.** Test cross-user nested IDs, signed URL access, malicious tenant redirects, replayed commands, forged receipts and stale approvals. Keep an append-only application action audit with restricted access and retention. Audit logging must not create an uncontrolled second store of candidate personal data.

## 12 Capacity reliability and cost controls

### Proposed service targets

| Area | Initial objective | Qualification |
| --- | --- | --- |
| Core metadata API | 99.9 percent monthly availability and p95 server latency below 500 ms | Excludes asynchronous rendering and employer response time; measure end-to-end separately |
| Accepted safe background work | 99 percent starts within two minutes under admitted load | Permission/user waits and scheduled scans reported separately |
| Covered-source freshness | 95 percent of active postings in the fixed covered-source scope verified within six hours | Outages remain in denominator; healthy-source metric is diagnostic |
| Submission safety | Zero unapproved sends, wrong recipients or known preventable duplicate sends | Hard incident gate, not proof of external exactly-once delivery |
| Recovery | Proposed RPO at most five minutes and RTO at most 60 minutes | Requires purchased backup capability and restore drill; not current guaranteed readiness |

**NF01 Admission.** Bound worker concurrency, database pools, render memory, file sizes, provider calls and per-candidate batches. Apply endpoint/credential-specific rate limits, Retry-After and jittered backoff. Separate ingestion, documents, generation and execution quotas so crawling cannot starve candidate actions.

**NF02 Fairness.** Use per-user and provider budgets and aged-work monitoring; queues do not guarantee FIFO execution. Maintain stable priority rules, prevent one batch from monopolizing capacity and expose delays. Redis can accelerate tokens, but critical launch checks remain durable. [S46]

**NF03 Scaling model.** Estimate safe concurrency from admitted throughput multiplied by measured average duration, then test variance and p95 resource use. For example, 1,000 prepared applications per hour is approximately 0.28 operations per second; a 90-second browser task implies about 25 concurrent sessions before headroom, but employer limits may dominate. This is a sizing example, not a sales capacity promise.

Start with indexed PostgreSQL and full-text search. Partition append-heavy events or snapshots and introduce a search service only after measured index size, relevance or latency requires it. Use bounded source coordinators rather than a Scheduler job or dedicated queue for every employer. Add regional workloads only after privacy, data consistency, latency and operating-cost review.

**NF04 Economics.** Report cost per fresh unique opening, relevant result, validated tailored resume and confirmed application. Separate licensing, search, network, database, storage, document rendering, browser execution and model costs. Measure cache effectiveness, retry waste and human intervention. No savings percentage is assumed before the baseline benchmark.

**NF05 Accounting.** Reserve and finalize product usage through durable idempotent ledger transitions; retries, coalesced work and duplicate receipts cannot debit twice. Show mode and expected charge before a paid operation. Define the reviewed policy for no-edit tailoring, partial forms, unknown submissions and provider failures. Zero model calls does not mean zero service cost or an automatically free product action.

**NF06 Performance and responsive quality.** Must support 320 px mobile, 390 px mobile, 768 px tablet, 1024 px laptop and 1440 px desktop without horizontal page overflow, clipped controls or inaccessible actions. Validate keyboard navigation, readable forms and reduced motion. Avoid eager chart/PDF/editor bundles, repeated profile requests on navigation and model work during page loads. Use route loading states, lazy heavy components, bounded result pages and private owner-scoped query caching. Target real-user p75 LCP at or below 2.5 seconds, INP at or below 200 milliseconds and CLS at or below 0.1 for launch routes, segmented by device and connection. These targets require measured field evidence; a local build or narrow synthetic run cannot prove production performance. Prevent personal data from entering shared caches.

## 13 Agent tools plugins and engineering setup

### Minimal required toolset

These are development requirements, not instructions to install everything now. Preserve repository lockfiles and versions; evaluate compatibility and licenses before adding libraries. Agent plugins support engineering work, while runtime connectors operate the candidate service. An installed recruiting plugin does not grant HireWiz universal access to employer tenants.

| Tool or integration | Requirement and purpose | Access and status |
| --- | --- | --- |
| Codex with repository and terminal tools | Required agent workspace, scoped edits, code review and documentation | Available in this session; follow repository instructions and reviewed scope |
| Git and GitHub CLI or GitHub connector | Required source control, PRs and CI inspection | GitHub plugin catalog reports installed; verify repository authorization during setup |
| Node and npm with lockfile | Required frontend build, types, lint and tests | Use project-compatible pinned runtime and npm ci |
| Python and uv | Required backend runtime, frozen dependency sync and tests | Use pyproject.toml and uv.lock; do not mutate lockfile accidentally |
| Docker and isolated local services | Required reproducible workers and integration fixtures | Local PostgreSQL/Redis and harmless mock employer endpoints |
| gcloud CLI and Terraform | Required reproducible GCP plans, deployment inspection and IaC | Scoped staging permissions and separate reviewed production release rights |
| Vercel CLI or Vercel plugin | Required frontend preview/release workflow; either interface suffices | Vercel skills available; project authentication must be verified |
| Web research and official vendor docs | Required contract, lifecycle and access verification | Built-in browsing is sufficient; record sources and checked dates |
| Browser test tooling | Required end-to-end UI and adapter verification | Existing Playwright, plus test employer sandboxes; computer-use tool for targeted inspection |

### Application libraries and verification tools

| Layer | Proposed tools | Why |
| --- | --- | --- |
| UI | Existing Next.js, React, TypeScript, Tailwind, React Hook Form and Zod | Extend current interface and validated review forms |
| API and persistence | Existing FastAPI, Pydantic, SQLAlchemy, Alembic and psycopg | Typed contracts, transactions, migrations and database constraints |
| Provider clients | Existing httpx plus versioned custom adapters and generated schemas where reliable | Explicit API contracts, timeouts, rate handling and dependency control |
| Native documents | Existing pdfplumber, pypdf, pypdfium2, python-docx and RapidFuzz | Source-aware parsing, conservative edits, rendering and exact-span matching |
| Document visual QA | Bundled document and PDF skills, isolated LibreOffice or equivalent Word-compatible renderer, raster and text inspection | DOCX pagination cannot be verified by python-docx alone; application LaTeX export also needs a sandboxed TeX runtime |
| Job extraction | JSON/XML parsers and targeted HTML parser such as lxml; Playwright only for necessary rendered sources | Avoid expensive browsers and AI for ordinary feeds |
| Optional AI | Existing google-genai/provider adapter and strict output validation | Keep model changes independent of authorization and execution |
| Backend tests | pytest, pytest-asyncio, HTTP fixtures or respx, property testing with Hypothesis where valuable | State races, protocol behavior and adversarial input verification |
| Frontend tests | Existing Vitest, Testing Library, Playwright and axe | Components, complete review flows and accessibility |
| Load and failures | k6 or Locust, deterministic fault injection, controlled queue replay | Capacity, DB saturation, retry and crash-boundary tests |
| Code quality and security | ESLint and TypeScript, Ruff, dependency audit, secret scanning, CodeQL or Semgrep, container scan such as Trivy | Reproducible quality gates and supply-chain review |
| Operations | Cloud Logging and Monitoring, existing Sentry, OpenTelemetry when useful | Correlated traces, redacted incidents, provider and cost dashboards |

New tools listed here are proposals; availability, license and compatibility checks belong to Stage 0. Choose one load tool and one primary static-security tool to avoid duplicate operational burden. Reuse existing packages first. Document any local learned models or OCR as AI and record their versions.

### Optional agent plugins

| Plugin | Use in development | Necessity and session evidence |
| --- | --- | --- |
| Figma | Inspect approved layouts, UI specifications and component mappings | Optional; Figma skills available, file access not yet verified |
| Codex Security | Additional security review and investigation | Optional; catalog reports available but not installed |
| Linear | Track stages, requirement IDs, acceptance evidence and defects | Optional; catalog reports available but not installed; GitHub issues are sufficient |
| Google Drive or document skills | Shared requirements and review artifacts | Optional; document generation tools available; cloud account access must be verified |
| Browser computer use | Inspect difficult UI flows and reproduce adapter failures | Available tool; does not replace deterministic production adapters |

OpenAI Developers is optional if we evaluate an OpenAI provider later; the present Gemini architecture does not require it. Slack, Teams, email and calendar plugins are not prerequisites for these stages. Notifications or receipt ingestion can be a separately consented feature later. Image-generation, Sites builders, Kubernetes tooling and unrelated storage plugins are not required for this development.

**AG01 Agent workflow.** Before a task, check repository status and instructions, read the affected contract, map requirement IDs and create a small reviewable branch or managed worktree. Use scoped tools and least-privilege environment access. Run relevant tests, inspect the complete candidate flow, attach evidence to the PR and update documentation. Avoid unrelated cleanup and concurrent edits to the same files.

**AG02 Environments.** Agents use synthetic candidates, fixtures and dedicated staging accounts. Never submit test applications to production employers or use production resumes as generic fixtures. Runtime job-application execution is a deterministic product workflow, not a Codex agent browsing indefinitely on behalf of every candidate.

GitHub Actions is the recommended CI runner already represented by the repository. Use Secret Manager, private artifact buckets, Artifact Registry, Cloud Tasks and Scheduler, and Cloud Logging/Monitoring as platform tools. Terraform is the proposed infrastructure authority. Standalone LaTeX documents authored by the agent use Codex's built-in editor/compiler; production resume conversion is a separate sandboxed application concern.

## 14 Deployment and operating strategy

**DP01 Environment isolation.** Maintain local, staging and production projects, credentials, queues, buckets and database boundaries. Terraform plans show IAM, workload identity, Cloud Run roles, Tasks, Scheduler, storage lifecycle, secrets references, monitoring and budget configuration. Keep current GCP and Vercel providers; no regional migration is included.

**DP02 Release sequence.** CI validates frozen dependencies, types/lint, relevant tests, OpenAPI compatibility, security and container builds. Run one controlled additive migration job using a direct database connection. Do not rely on session advisory locks through transaction pooling. Deploy backward-compatible worker revisions and queue payload versions, then API canary and the exact verified frontend build. Complete end-to-end staging acceptance before production promotion.

**DP03 Progressive enablement.** Release behind separate discovery, preparation, optional AI, browser and submit flags. Begin with internal fixtures, authorized pilot employers and small candidate cohorts. Expand connector by connector and geography by geography based on coverage and receipt evidence. Provide per-connector and global execution kill switches that block new launch permits, not just UI buttons.

**DP04 Rollback.** Roll back compatible application revisions and disable unsafe connectors; do not blindly reverse data migrations. Stop new sends while reconciling in-flight attempts. Keep already-created packages and receipts inspectable. Outbox and task schema compatibility must be tested across deployment versions.

**DP05 Recovery.** Verify actual database backup entitlement, retention, object versions and restore procedures. A database restore can lose recorded send attempts while employers retain applications. Pause all submissions, rotate a recovery epoch stored outside the restored database’s rollback scope, fence old workers and queues, and quarantine the uncertain window. Reconcile provider receipts before reactivation; a missing restored row is not proof that nothing was sent.

Reapply independently retained deletion and revocation tombstones before account access or execution resumes. Invalidate outstanding approvals and browser commands from before recovery and obtain fresh per-job approval. Restore must not resurrect a deleted candidate account, revoked evidence or withdrawn consent. Minimize and protect the independent tombstone store under the retention policy.

An independent minimal launch/receipt journal can assist reconciliation but does not create an atomic external transaction. Restrict and retain it deliberately. Practice recovery without generating new employer applications, and document measured RPO/RTO before advertising them.

## 15 Development stages and verification checklist

The stages describe work to start only after product review. Suggested ownership is product, backend, frontend/browser, platform and QA/security; small teams may share roles. Access agreements and coverage expansion are parallel business dependencies, not engineering completion claims. Checklist items are unchecked intentionally. Record test or PR evidence, reviewer and date for each item.

### Stage 0 Scope baseline and contract approval

Freeze pilot cohorts, source policy, supported file types, candidate controls and budget targets. Recheck deployed configuration and vendor contracts. Set up required agent tools and isolated fixtures. No application automation should begin before source and write permission records exist.

- [ ] G00A Approve scope, origin policy, optional locator decision, adult eligibility and coverage definitions. Evidence: signed review record. Covers CV01 and product decisions.
- [ ] G00B Capture runtime, model attempts, latency, costs, migrations and current API/UI baseline. Evidence: redacted baseline report. Covers AI06 and NF04.
- [ ] G00C Verify lockfiles, local CI parity, GitHub/GCP/Vercel access and minimal tooling; document optional plugins. Evidence: setup runbook. Covers AG01 and DP01.
- [ ] G00D Create source/grant inventory, vendor checked dates and permitted sandbox plan. Evidence: registry and contract checklist. Covers CN01–CN03.

### Stage 1 Reliable core and AI efficiency

Implement durable outbox and workload budgets, deterministic facts and basic-fit contracts, actual attempt telemetry, scoped result reuse and the public fallback fix. Move expensive processing outside request and database lock boundaries. Keep existing enhanced behavior behind a reviewed mode during evaluation.

- [ ] G01A Verify cold-cache LLM-disabled fresh resume and manual fact correction flow. Evidence: failing-on-unexpected-call test. Covers FR01, AI01–AI03.
- [ ] G01B Verify parser edge cases, useful basic ranking and unchanged-result coalescing. Evidence: labeled corpus and evaluation report. Covers FR04, AI02, AI05.
- [ ] G01C Prove outbox crash recovery, duplicate-task handling, bounded DB pools and total provider attempt cap. Evidence: fault-injection logs. Covers SD01–SD02, AI04.
- [ ] G01D Replace invented fallback; verify exact billing transitions and no generation on navigation. Evidence: route tests and telemetry. Covers AI06–AI08.

- [ ] G01E Verify independent paid service balance, captured-payment grants, concurrent reservations, proportional refunds, search/application settlement and expense-policy margin floors. Covers BI01–BI08.

### Stage 2 Verified discovery pilot

Build employer registry, scheduler/coordinator, public connector families, normalization, full-scan integrity and search UI. Start with Greenhouse, Lever, Ashby and SmartRecruiters, then add high-yield public Workable, Personio and Pinpoint sources. Partner work proceeds in parallel; no submit capability is implied by discovery completion.

- [ ] G02A Verify employer-to-tenant links, regional hosts, public listing filters and SSRF protections. Evidence: connector fixtures and origin audit. Covers CN01, SE02.
- [ ] G02B Verify pagination, multilingual/location fields, dates, deduplication, closure and outage behavior. Evidence: complete-scan fixtures. Covers CV06–CV07, SD04.
- [ ] G02C Verify independent employer/posting/query recall and publish scope, misses and uncertainty. Evidence: coverage audit. Covers CV01–CV05.
- [ ] G02D Verify search cursors, filters, provenance, capability labels and accessible results. Evidence: end-to-end search tests. Covers FR03 and FR10.

### Stage 3 Resume preparation and review packages

Improve native format preservation, tailored edits and sealed artifact generation. Add per-job selection before and after tailoring, form schemas, confirmed answers and exact review. Support original/custom without AI. Do not begin sends in this stage.

- [ ] G03A Compare PDF, DOCX and supported LaTeX fixtures before/after; validate truthful useful edits and unsupported-file guidance. Evidence: visual and extraction report. Covers RS01–RS06 and RS08.
- [ ] G03B Prove preview/download/upload identities use the exact sealed hash and object version. Evidence: artifact contract tests. Covers RS07 and SD03.
- [ ] G03C Verify per-job resume changes, candidate-written answers, conditional form fields and evidence revocation. Evidence: review-flow tests. Covers FR05–FR07.
- [ ] G03D Verify package approval binding and invalidation on changed form, consent, job, answer or file. Evidence: version-race tests. Covers EX05 and SD05.

### Stage 4 Permitted browser assistance

**Launch-order decision, 8 October 2026:** the candidate selected reviewed browser
assistance first. See [the delivery decision](../delivery/INITIAL_BROWSER_ASSISTANCE_2026-10-08.md).
Final submission stays candidate-controlled; fill-only/user-reported outcomes cannot
settle the automatic-application charge or close Stage 5. Production pairing, independent
authority and permitted adapters remain required before enabling Stage 4.

Build a narrowly scoped browser companion for tested employer career adapters. Keep candidate sessions local, support checkpoint recovery and pause for human work. Stage 4 remains fill-only or candidate-submit. Any automatic send, including a browser pilot, must first pass G05A–D and the relevant recovery gates.

- [ ] G04A Verify tenant-specific origin permissions, device-bound commands and local session isolation. Evidence: extension security tests. Covers EX02 and SE03.
- [ ] G04B Verify conditional fields, uploads, autosave approval and missing-input pauses. Evidence: authorized form fixtures. Covers FR06 and EX05.
- [ ] G04C Verify extension restarts, account login, CAPTCHA/MFA and assessment handoffs without replaying possible sends. Evidence: interrupted-flow tests. Covers EX02 and EX07.
- [ ] G04D Verify fill-only and assisted-submit labels, accessibility and candidate cancellation. Evidence: usability review. Covers FR09–FR10 and EX04.

### Stage 5 Authorized automatic submissions

Enable employer or partner APIs only after grants and application parity are verified. Implement launch permits, explicit partial outcomes, receipt validation and reconciliation. Start with a small set of authorized tenants rather than enabling a whole ATS family indiscriminately.

- [ ] G05A Validate each tenant grant, required fields, applied status, file limits and provider receipts in a sandbox. Evidence: connector release record. Covers CN02–CN04 and EX01.
- [ ] G05B Prove duplicate delivery cannot launch another possible send; test crash before/after external request. Evidence: fault-injection matrix. Covers EX06–EX07.
- [ ] G05C Prove cancellation races, stale approval, account deletion and late valid receipts resolve safely. Evidence: state-transition tests. Covers EX05, EX08–EX09 and SE05.
- [ ] G05D Verify per-job batch approval, candidate limits, actual confirmations and connector kill switches. Evidence: controlled pilot metrics. Covers FR07–FR09 and DP03.

- [ ] G05E Verify visible per-job and batch quotes, immutable price/cost snapshots, holds on unknown outcomes, below-floor pack/promotion refusal and audited reconciliation without a repeated send. Covers BI02–BI08.

### Stage 6 Wider coverage and production readiness

Expand measured employer populations, enterprise ATS agreements, India-specific portals and jurisdiction support. Add licensed locators only after separate approval. Validate load, recovery, privacy and commercial economics before marketing broad coverage.

- [ ] G06A Prove greater than 95 percent discovery recall within each claimed scope under the independent audit gate. Evidence: strata and confidence report. Covers CV01–CV05.
- [ ] G06B Publish discovery, preparation, automatic availability and confirmed execution metrics separately, including unsupported jobs. Evidence: coverage dashboard. Covers CV05 and EX09.
- [ ] G06C Verify load admission, source outages, pool saturation, provider throttling and monthly SLO reporting. Evidence: load and resilience report. Covers NF01–NF04.
- [ ] G06D Complete redacted security, retention, deletion, accessibility and restore drills, including independent tombstones and fresh approvals. Evidence: release signoff. Covers SE01–SE06, AI09 and DP01–DP05.

- [ ] G06E Verify all declared device widths, route bundle/loading behavior and measured performance budgets before promotion. Covers FR10 and NF06.

### Stage 7 Continuous improvement

Maintain contract monitoring, source acquisition and candidate feedback. Add optional AI only where relevance or writing evaluations demonstrate useful gains. Investigate missed jobs, form failures and costs before adding complex infrastructure.

- [ ] G07A Review missed-job strata and stale/retired vendor contracts monthly; prioritize measured marginal yield. Evidence: connector backlog.
- [ ] G07B Compare basic and enhanced analysis against independent human judgments and candidate outcomes. Evidence: evaluation changes and budget decision. Covers AI03–AI06.
- [ ] G07C Revalidate source permissions, adapters, native document fixtures and incident runbooks after changes. Evidence: maintenance log. Covers CN03 and DP03–DP05.

Indicative staffing is two product engineers plus fractional QA/security/platform support. A discovery and preparation pilot may take approximately four to six weeks after Stage 0 foundations are validated; browser assistance may extend cumulative work to eight to twelve weeks. Authorized API submissions can require another three to six engineering weeks after actual access is granted. These are planning estimates with low confidence until baseline work is sized, and do not promise worldwide coverage or a partnership timeline.

## 16 Evaluation release gates and delivery evidence

### Required test families

Use synthetic fixtures and authorized sandboxes. Preserve source provenance for every test expectation and keep real candidate data out of generic evaluation sets unless separately consented and minimized.

| Test family | Failure cases and evidence |
| --- | --- |
| Discovery | Truncated pagination, ambiguous/reposted dates, missing markup, regional tenants, duplicates, closed jobs, outage and unknown employers |
| Extraction and fit | Short skills, negation, nontechnical roles, overlap dates, location/name separation, false exclusions and relevant unseen jobs |
| Native output | Long text, embedded fonts, columns, tables, hyperlinks, glyphs, overflow and wrong reading order; visual before/after review |
| Form parity | Required and conditional questions, files, video, assessments, demographic choices, policy versions and incomplete successful responses |
| Authorization | Changed package, wrong tenant, revoked evidence, stale device, cancelled account, cross-user IDs and forged receipt |
| Execution | Duplicate task, expired lease, crash before/after send, 5xx/timeout, receipt delay, cancellation race and provider idempotency boundaries |
| Operations | DB/Redis outage, queue saturation, budget exhaustion, cloud rollback, restored lost attempts and global kill switch |
| AI efficiency | Cold-cache zero-LLM path, warm AI-derived reuse, invalidation, total attempts, tokens, safe partial result and no invented fallback |

### Release evidence package

For every stage, produce a PR linked to requirement and checklist IDs, schema/API change summary, migration and rollback notes, automated test results, representative screenshots or render evidence, source/permission checks and known limitations. Product and engineering reviewers sign the stage gate with evidence links and dates. Every provider release has a capability record showing which tenants, regions and form versions were tested.

Critical launch incidents include an unapproved disclosure, wrong employer, fabricated candidate answer, preventable duplicate possible send or false confirmed status. Disable affected execution immediately and reconcile. Relevance regressions and missed postings receive measured review thresholds; do not obscure safety failures behind a high average completion rate.

The checklist is for verifying development later, not a claim that work is complete. Acceptance evidence should distinguish code tested locally, integration tested in staging and observed pilot behavior. Source research proves vendor documentation, not successful implementation.

## 17 Risks dependencies and review record

| Risk or dependency | Design response | Remaining tradeoff |
| --- | --- | --- |
| No worldwide job census | Scoped independent recall audits and employer acquisition | Cannot honestly guarantee all global recent jobs |
| Employer write access | Partner agreements, per-tenant grants and layered modes | Automatic availability grows slower than discovery |
| Portal changes and challenges | Versioned adapters, canaries and human handoff | Browser maintenance and intervention costs remain |
| PDF editing constraints | Conservative edits, visual validation and explicit alternative choice | Exact layout cannot always support longer improved wording |
| External uncertain sends | Durable launch state, no blind retry and reconciliation | Some outcomes remain unknown until verified |
| AI removal quality loss | New basic contract and independent evaluation | Nuanced writing and transferable analysis may still benefit from AI |
| Existing cross-region database | Measure pools/latency and preserve deployment initially | Network latency and egress remain until future review |
| Cloud or database restore | External recovery epoch and uncertain-window quarantine | Restore does not reverse an employer application |
| Licensing and privacy | Source rights register and jurisdiction review | Permissions and regional obligations can limit rollout |

Review record to complete before development:

- [ ] Approve initial countries, languages, role families, eligible employer population and independent coverage method.
- [ ] Approve employer-origin default and decide whether licensed locator expansion is allowed.
- [ ] Approve original/custom/tailored control, supported file capabilities and explicit alternative-layout policy.
- [ ] Approve per-job review, disclosure/send actions, batch/daily limits and human handoffs.
- [ ] Approve AI feature defaults, operation budgets, product charging and evaluation criteria.
- [ ] Approve required tooling, staging access, optional plugin choices, retention and deployment gates.
- [ ] Assign product and engineering owners, source partnership owner and QA/security reviewers.
- [x] Development and deployment authorized by the product owner on 8 October 2026. Record verified stage evidence and remaining external grants in the delivery checklist.

## 18 Research sources and local evidence

Official sources checked on 8 October 2026. Access, limits, products and commercial rights can change; recheck before connector release. References describe researched contracts, not grants obtained by HireWiz. Source IDs placed near requirements identify supporting documentation; recommendations and proposed targets are our design decisions.

### Employer feeds and ATS contracts

S01 Greenhouse Job Board API — https://docs.greenhouse.io/job-board.html

S02 Lever Postings API — https://github.com/lever/postings-api

S03 Ashby public job posting API — https://developers.ashbyhq.com/docs/public-job-posting-api

S04 Ashby dedicated partner feeds — https://developers.ashbyhq.com/docs/dedicated-partner-job-feeds

S05 Ashby application form submission — https://developers.ashbyhq.com/reference/applicationformsubmit

S06 SmartRecruiters posting endpoints — https://developers.smartrecruiters.com/docs/endpoints

S07 SmartRecruiters Application API — https://developers.smartrecruiters.com/docs/application-api

S08 Workable public career page integration — https://help.workable.com/hc/en-us/articles/115012771647-Using-the-Workable-API-to-create-a-careers-page

S09 Workable application form — https://workable.readme.io/reference/jobsshortcodeapplication_form

S10 Workable candidate creation — https://workable.readme.io/reference/job-candidates-create

S11 Personio open positions XML — https://developer.personio.de/docs/retrieving-open-job-positions

S12 Personio recruiting application API — https://developer.personio.de/v1.0/reference/post_v1-recruiting-applications

S13 Pinpoint public postings — https://developers.pinpointhq.com/docs/jobs-json-endpoint

S14 Pinpoint applications — https://developers.pinpointhq.com/reference/post-applications

S15 Recruitee Careers authentication transition — https://docs.recruitee.com/reference/authentication-1

S16 Recruitee candidate application — https://docs.recruitee.com/reference/offersoffer_idcandidates

S17 Teamtailor API scopes and answer support — https://docs.teamtailor.com/

S18 BambooHR job summaries — https://documentation.bamboohr.com/reference/get-job-summaries

S19 BambooHR candidate creation — https://documentation.bamboohr.com/reference/create-candidate

S20 Breezy authorization — https://developer.breezy.hr/reference/authorization

S21 Breezy applied candidate semantics — https://developer.breezy.hr/reference/addcandidate

S22 JazzHR partner Apply API — https://apidoc.jazzhrapis.com/custom-apply/

S23 Comeet Careers API — https://developers.comeet.com/reference/careers-api-overview

S24 Comeet application widget — https://developers.comeet.com/reference/application-form-widget

S25 Zoho Recruit record insertion — https://www.zoho.com/recruit/developer-guide/apiv2/insert-records.html

S26 Zoho Recruit submission definition — https://www.zoho.com/recruit/developer-guide/apiv2/create-submission.html

S27 Freshteam sunset FAQ — https://support.freshteam.com/support/solutions/articles/19000162935-freshteam-sunset-support-faqs

S28 Keka Hire jobs — https://developers.keka.com/reference/get_v1-hire-jobs

S29 RippleHire integrations — https://www.ripplehire.com/ats-integrations

S30 Darwinbox recruitment — https://darwinbox.com/products/recruitment

S31 TurboHire product — https://new.turbohire.co/

### Discovery and regional sources

S32 Schema JobPosting — https://schema.org/JobPosting

S33 Google job posting structured data — https://developers.google.com/search/docs/appearance/structured-data/job-posting

S34 Google Indexing API scope — https://developers.google.com/search/apis/indexing-api/v3/using-api

S35 USAJOBS Search API and limits — https://developer.usajobs.gov/api-reference/get-api-search and https://developer.usajobs.gov/guides/rate-limiting

S36 NCS direct employer versus partner jobs — https://www.ncs.gov.in/job-seeker/_layouts/15/ncsp/faqs.aspx

S37 UK Civil Service recruitment — https://www.gov.uk/government/organisations/civil-service/about/recruitment

S38 Canada federal application guidance — https://www.canada.ca/en/public-service-commission/jobs/services/gc-jobs/applying-government-canada-jobs-how-to-apply.html

S39 EU institution vacancies — https://selection.eu-careers.europa.eu/en/job-opportunities/open-vacancies/temp

S40 Brave Web Search documentation — https://api-dashboard.search.brave.com/app/documentation/web-search/codes

S41 Google Custom Search availability — https://developers.google.com/custom-search/v1/overview

S42 Bing Search retirement — https://learn.microsoft.com/lifecycle/announcements/bing-search-api-retirement

S43 Adzuna search documentation — https://developer.adzuna.com/docs/search

S44 LinkedIn Job Posting access — https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview?view=li-lts-2025-10

S45 Indeed partner onboarding — https://docs.indeed.com/job-sync-api/integrate-with-job-sync-api

### Platform engineering

S46 Cloud Tasks execution limitations — https://docs.cloud.google.com/tasks/docs/common-pitfalls

S47 GCS object immutability and generations — https://cloud.google.com/storage/docs/objects

S48 Chrome extension service workers — https://developer.chrome.com/docs/extensions/develop/migrate/to-service-workers

S49 GCP Secret Manager best practices — https://cloud.google.com/secret-manager/docs/best-practices

### Enterprise platform sources

S50 Workday external career sites and integration security — https://doc.workday.com/admin-guide/en-us/human-capital-management/recruiting/career-sites/san1431625385171.html and https://doc.workday.com/workday-education/en-us/course-manuals/creating-and-securing-integrations/workday-configurable-security.html

S51 Oracle CE internal-use endpoint notice — https://docs.oracle.com/en/cloud/saas/human-resources/farws/api-recruiting-ce-job-requisitions.html

S52 Oracle partner Direct Apply provisioning — https://docs.oracle.com/en/cloud/saas/talent-management/faimh/set-up-partner-integration-provisioning.html

S53 Taleo Sourcing implementation guide release 24B — https://docs.oracle.com/en/cloud/saas/taleo-enterprise/24b/tsscg/implementing-sourcing.pdf

S54 SAP JobApplication permissions and operations — https://help.sap.com/docs/successfactors-platform/sap-successfactors-api-reference-guide-odata-v2/jobapplication

S55 SAP deprecated Recruiting XML feed notice — https://userapps.support.sap.com/sap/support/knowledge/E/2428902

S56 iCIMS standard approved job feed — https://developer-community.icims.com/platform/services/standard-xml-feed-job-boards?sort_by=changed

S57 iCIMS Apply Framework and completion — https://developer-community.icims.com/platform/frameworks/icims-apply-framework/apply-flows-jobs-and-job-applications and https://developer-community.icims.com/application-complete-notification

S58 Jobvite employer feed and application guide — https://careers.jobvite.com/careersite/job_feed_api.html

S59 Avature integration framework — https://www.avature.net/blogs/how-to-succeed-hr-technology-integration/

S60 IBM historical BrassRing integration index — https://www.ibm.com/support/pages/ibm-kenexa-brassring-cloud-technote-table-contents

S61 ADP product-specific application API guides — https://developers.adp.com/articles/preview/guide-about-this-api-0?chapter=1 and https://developers.adp.com/guides/api-guides

S62 UKG developer access configuration — https://developer.ukg.com/proplatform/docs/developer-console-quick-start

S63 Dayforce employer enablement and application receipt semantics — https://help.dayforce.com/r/ImplementationGuide/Dayforce-Implementation-Guide/Configure-the-Allow-Candidates-and-Job-Application-Sourcing-from-External-Job-Boards-Client-Property?contentId=fwymP~ZFo6xr61UOmPJBJQ and https://help.dayforce.com/r/documents/Dayforce-Web-Services-Introduction-Guide/RESTful-POST-Candidate-Sourcing

### Local planning and repository evidence

L1 Original employer discovery and application plan — /Users/harshil/.codex/artifacts/hirewiz/2026-10-07/hirewiz-employer-job-discovery-and-application-plan.md

L2 System design — /Users/harshil/.codex/worktrees/hirewiz-area-redesign/ai-resume-copilot/docs/EMPLOYER_DISCOVERY_APPLICATION_SYSTEM_DESIGN.md

L3 AI efficiency audit — /Users/harshil/.codex/worktrees/hirewiz-area-redesign/ai-resume-copilot/docs/AI_CALL_EFFICIENCY_AUDIT.md

L4 Repository baseline d5c2ea884775c85c0064ed5c8765a072cdcd0fa2 — backend/app/services/llm_client.py, domains/analysis, services/parsing.py, services/resume_layout.py, services/resume_artifacts.py, domains/career and frontend/package.json. Local paths are reference evidence, not portable dependencies of this Word document.

# HireWiz Product and Architecture Upgrade Plan

> Product: HireWiz
> Repository: `Harshil4442/ai-resume-copilot`
> Status: Implemented baseline; production rollout and outcome validation pending
> Last updated: 2026-08-03
> Planning horizon: 6 to 7 months, delivered incrementally

## Executive Summary

HireWiz should evolve from a collection of AI job-search tools into an evidence-backed Career Workspace. The product should help a candidate move an opportunity from discovery through application, interview preparation, and outcome tracking while preserving the exact evidence, resume version, learning plan, and decisions attached to that opportunity.

The near-term objective is not to add every possible feature. It is to make the existing product dependable, modern, measurable, and differentiated enough to support paid growth. The recommended sequence is:

1. Stabilize the current platform and move unsupported dependencies onto supported releases.
2. Introduce reliable asynchronous AI execution, usage accounting, observability, and database migrations.
3. Complete a full visual and interaction redesign while retaining the HireWiz name.
4. Improve onboarding and activation around one clear first-value experience.
5. Build the Career Workspace as the flagship product surface.
6. Add evidence-aware AI, skill ROI, career memory, and outcome learning.
7. Optimize monetization only after product usage, AI cost, retention, and conversion are measurable.

The current Vercel frontend and Google Cloud backend are suitable for this stage. The backend should remain a modular monolith rather than being split into microservices. Cloud Tasks and a private Cloud Run worker provide enough asynchronous processing capacity without introducing Kubernetes or a separate orchestration platform.

### Product Goal

Build the most trusted workspace for turning a candidate's real experience into better job-search decisions and stronger applications.

### Business Goal

Create a high-margin consumer subscription and one-time purchase business in India first, while keeping the data model, payments boundary, and product architecture ready for global expansion.

### North-Star Outcome

The primary product outcome is a qualified application completed with a role-specific, evidence-backed resume and preparation plan. Page views, generated text, and raw analysis counts are supporting metrics, not the north star.

### Planning Principles

- Ship incremental releases behind feature flags.
- Improve reliability before increasing acquisition spend.
- Keep AI outputs grounded in user-approved evidence.
- Preserve user ownership, editability, and exportability of career data.
- Measure activation, retention, outcomes, AI cost, and payment reliability.
- Prefer established, supported technologies over fashionable infrastructure.
- Keep source expansion, international billing, B2B tenancy, and voice features out of the initial upgrade.

## Architecture Decisions

### 1. Retain the Deployment Split

Keep the current high-level deployment model:

```text
Browser
  |
  v
Next.js on Vercel
  |  same-origin BFF requests
  v
FastAPI API on Google Cloud Run
  |------------------|------------------|
  v                  v                  v
PostgreSQL          Redis          Cloud Tasks
                                          |
                                          v
                              Private Cloud Run worker
                                          |
                                          v
                              AI and external providers
```

This model provides independent scaling for the user interface and AI workload while retaining a simple operational boundary.

### 2. Use a Modular Monolith

Keep one deployable FastAPI application and organize it into explicit domain modules:

- Identity and accounts
- Resumes and evidence
- Opportunities and applications
- Match and analysis
- Learning and interview preparation
- Billing and entitlements
- Usage accounting
- Notifications
- Administration and support

Each module should own its routes, service layer, persistence code, schemas, and tests. Cross-module operations should go through service interfaces rather than importing route handlers or mutating another module's tables directly.

Do not introduce microservices until independent scaling, deployment ownership, or regulatory isolation is demonstrably required. A solo founder should not pay the coordination cost of distributed systems before the product creates that need.

### 3. Frontend Target

Adopt the following supported frontend baseline:

- Node.js 24 LTS
- Next.js 16 Active LTS
- React 19.2
- Strict TypeScript
- Tailwind CSS 4
- shadcn/ui primitives customized into a HireWiz design system
- Lucide icons
- Motion only for meaningful transitions and progress feedback
- TanStack Query for server state
- React Hook Form and Zod for form state and validation
- `openapi-typescript` and `openapi-fetch` for generated API contracts

Upgrade Next.js 14 to 15 and then 16 in separate changes. Do not combine a framework upgrade with the full redesign. Each framework step must pass build, authentication, route, and Playwright checks before proceeding.

### 4. Same-Origin Backend-for-Frontend

Private browser requests should go to Next.js route handlers or server actions on the HireWiz origin. Those server-side handlers should call the Cloud Run API using a server-only backend URL and credential.

Benefits:

- Backend bearer tokens and service credentials never enter browser JavaScript.
- Authentication refresh and error normalization have one owner.
- Browser CORS complexity is reduced.
- The frontend can evolve independently from backend response details.
- Rate limiting and request correlation can start at the public edge.

`BACKEND_URL` remains server-only. `NEXT_PUBLIC_API_BASE_URL` should be deprecated for authenticated APIs once equivalent BFF routes exist. Public, cacheable endpoints may remain directly accessible when there is a clear performance reason.

### 5. Backend Target

Adopt the following backend baseline:

- Python 3.12
- FastAPI with explicit domain routers and services
- SQLAlchemy 2
- Psycopg 3
- Alembic migrations
- Pydantic schemas at API and provider boundaries
- `httpx` for external HTTP calls
- `pyproject.toml` and a committed `uv.lock`
- Ruff for linting and formatting
- Static type checking for service and domain code

Replace startup `Base.metadata.create_all` and custom production migration logic with versioned Alembic migrations. `create_all` can remain temporarily in isolated tests while the migration test harness is introduced.

External I/O should use bounded timeouts, retries only for retry-safe operations, correlation IDs, and structured provider errors. Long-running provider calls must not hold an interactive request open.

### 6. Asynchronous Analysis Execution

Use Cloud Tasks to dispatch long-running work to a private Cloud Run worker. Persist every operation in `analysis_runs` before enqueueing it.

Expected flow:

1. Validate authentication, ownership, input, entitlement, and estimated usage.
2. Create an `analysis_runs` row and reserve usage units in one database transaction.
3. Enqueue a Cloud Task using the run ID as the idempotency key.
4. Return HTTP `202 Accepted` with the run ID.
5. Let the worker claim and process the run.
6. Persist structured output and model-call telemetry.
7. Commit reserved usage on success or release it on terminal failure.
8. Notify the client through polling initially and server-sent events when justified.

The worker must be idempotent. Duplicate task delivery must not create duplicate output or charge usage twice.

### 7. Data and Storage

- PostgreSQL remains the source of truth for users, product state, entitlements, usage, and outcomes.
- Redis remains an optimization for caching, rate limiting, and short-lived coordination. It must not be the sole store for billable state.
- Google Cloud Storage should hold private uploaded and generated files through signed, short-lived URLs.
- Store normalized structured AI results in PostgreSQL and large immutable artifacts in object storage.
- Add `pgvector` only after a measured retrieval use case outgrows indexed relational and full-text queries.

Every user-owned record must carry an owner or organization boundary, creation and update timestamps, and an explicit deletion strategy.

### 8. Observability and Product Analytics

Use complementary tools with clear ownership:

- PostHog for product events, funnels, cohorts, experiments, and feature flags.
- Sentry for frontend and backend exceptions, traces, and release health.
- Google Cloud Logging for structured operational logs and worker execution details.
- PostgreSQL model-call events for durable AI cost, latency, model, and prompt-version records.

Every request and asynchronous run should carry a correlation ID. Logs must not contain resume text, raw secrets, payment credentials, or unnecessary personal data.

### 9. Security and Trust Baseline

- Store secrets only in Vercel environment variables and Google Secret Manager or Cloud Run secrets.
- Rotate any credential exposed in screenshots, chat, commits, or logs.
- Keep Razorpay key secrets and webhook secrets exclusively in the backend.
- Verify webhook signatures against the raw request body.
- Make payment and usage mutations idempotent and append-only where practical.
- Add per-user and per-IP rate limits to expensive and authentication-sensitive routes.
- Validate file type, size, ownership, and scanning policy before processing uploads.
- Define retention and deletion behavior for resumes, job descriptions, generated content, logs, and backups.
- Add account export and deletion workflows before global expansion.

### 10. Explicitly Deferred Technologies

Do not add these during the foundation and Career Workspace phases:

- Kubernetes
- GraphQL
- Independently deployed microservices
- A dedicated vector database
- A broad LangChain-style abstraction layer
- Multi-region active-active deployment
- WebRTC voice interviews
- A browser extension
- Automatic job applications
- B2B organization tenancy
- International payment providers

These are valid later options, but they do not solve the current reliability, activation, differentiation, or retention problems.

### 11. Architecture Decision Records

Create short ADRs as implementation begins:

- ADR-001: Vercel plus Cloud Run deployment boundary
- ADR-002: Modular monolith domain ownership
- ADR-003: Next.js same-origin BFF
- ADR-004: Cloud Tasks analysis execution
- ADR-005: Usage reservation ledger
- ADR-006: Evidence-backed AI policy
- ADR-007: PostgreSQL and object storage ownership

Each ADR should record context, decision, alternatives, consequences, and reversal conditions.

## Product Direction

### 1. Positioning

HireWiz should not compete as another resume generator. Resume generation is increasingly commoditized. The differentiated promise is:

> HireWiz turns a candidate's real evidence into a role-specific application strategy and keeps the entire journey connected until an outcome is known.

The product should feel like a focused operating workspace, not a gallery of unrelated AI tools.

### 2. Flagship: Career Workspace

The Career Workspace is the primary product surface. One opportunity should connect:

- A stable job snapshot
- Company, role, source, location, compensation, and deadline
- Match analysis and confidence
- Required, demonstrated, and missing skills
- The exact resume version used
- Evidence supporting every important claim
- Cover note or outreach drafts
- Interview questions and preparation material
- Relevant learning actions
- Contacts, notes, reminders, and follow-ups
- Application stage and event history
- Final outcome and lessons learned

Suggested stages:

```text
Saved -> Evaluating -> Preparing -> Applied -> Interviewing -> Offer
                                      |              |
                                      v              v
                                   Rejected       Withdrawn
```

Transitions should be recorded as events so the system can calculate conversion, time in stage, and outcome patterns without overwriting history.

### 3. Evidence Graph

Create an Evidence Graph from user-approved facts such as:

- Roles and responsibilities
- Projects
- Achievements
- Skills and tools
- Education and certifications
- Quantified outcomes
- Portfolio links and work samples

AI-generated resumes, cover notes, and interview answers may transform and select evidence, but must not invent it. When evidence is weak or missing, the interface should ask the user for a fact instead of silently fabricating one.

Every generated claim should be traceable to one or more evidence items. Users should be able to approve, edit, reject, and reuse evidence.

### 4. Skill ROI

Generic skill-gap lists are not enough. Skill ROI should rank learning actions using:

- Frequency across saved target roles
- Importance within those roles
- Current evidence strength
- Estimated time to useful competence
- Availability and cost of learning resources
- Salary or opportunity impact where reliable data exists
- User constraints and target timeline

The result should answer, "What should I learn next, and why is it worth my time?"

### 5. Career Memory

Career Memory should be explicit and user-controlled. It may remember:

- Preferred role families and locations
- Compensation expectations
- Work authorization and employment preferences
- Approved evidence and writing preferences
- Target companies
- Rejected recommendations and stated reasons
- Reusable interview stories

Memory entries need provenance, edit controls, deletion, and a visible distinction between user facts and system inferences. Do not create an invisible permanent profile from every interaction.

### 6. Outcome Learning

Ask users to record application outcomes with low-friction prompts. Use aggregate outcomes to improve recommendations, not to claim causation from weak data.

Useful outcome questions include:

- Was the application submitted?
- Was there a recruiter response?
- Was an interview offered?
- Which resume version was used?
- Which stage ended the process?
- What feedback was received?
- Was an offer made or accepted?

Outcome learning should initially personalize within the user's own history. Cross-user recommendations require privacy review, sufficient sample size, and bias monitoring.

### 7. Existing Features to Keep and Reframe

Keep the strongest existing capabilities, but place them in context:

| Existing area | Future role |
| --- | --- |
| Resume | Versioned resume and Evidence Graph editor |
| Match | Opportunity-specific analysis inside Career Workspace |
| Market | Research and discovery input, not the core workflow |
| Learning | Skill ROI actions connected to target opportunities |
| Billing | Entitlements, usage history, invoices, and transparent limits |
| Dashboard | Priorities, active opportunities, reminders, and recent outcomes |

Avoid adding more top-level navigation items for each AI operation. Most operations should appear where the user needs them inside an opportunity.

### 8. First-Value Journey

The initial activation flow should be:

1. Create an account or sign in.
2. Upload or build a resume.
3. Review extracted evidence.
4. Add a target job by URL, paste, import, or manual entry.
5. Receive a match with a short, actionable explanation.
6. Create the first opportunity workspace.
7. Choose the next action: improve evidence, tailor a resume, learn a skill, or prepare outreach.

Target time to first useful match: under 10 minutes.

### 9. Full Brand and Experience Redesign

Keep the HireWiz name while replacing the current generic AI visual language. The redesign should include:

- A new color system with neutral work surfaces and restrained brand accents
- A readable type scale with compact application headings
- Consistent spacing, borders, focus states, and icon sizing
- A responsive application shell for desktop and mobile
- Clear loading, empty, partial, error, offline, and success states
- Accessible charts and progress indicators
- Motion limited to transitions, progress, and state change
- Real product visuals on public pages
- Plain language focused on outcomes and trust

Avoid decorative gradient orbs, deeply nested cards, excessive glass effects, and one-note purple or blue palettes. Operational screens should prioritize scanning, comparison, and repeated action.

### 10. Monetization Direction

Retain the current INR 999, 30-day Premium pass during the foundation work. Changing architecture, positioning, packaging, and pricing simultaneously would make conversion results difficult to interpret.

Before changing the offer, measure:

- AI and infrastructure cost per activated user
- AI cost by operation
- Free-to-paid conversion
- Premium activation and repeat usage
- Refund and payment failure rates
- Retention after 7, 30, and 60 days
- Conversion by acquisition channel
- Gross margin per paid cohort

After reliable measurement, test packages such as:

- Free: profile, evidence setup, limited matches, and workspace preview
- Pro monthly: active workspace, tailoring, preparation, and larger fair-use limits
- Job Sprint: a time-boxed package for an intensive search
- Expert review add-on: marketplace or partner review with clear quality control
- Referral credit: awarded only after a verified paid conversion

Do not promise unlimited AI usage until cost distribution and abuse patterns are known. Any change to existing Premium terms requires a grandfathering policy, updated product copy, support readiness, and payment-provider review.

## Implementation Roadmap

The phases overlap intentionally, but each release must satisfy its acceptance gate before reaching all users.

### Phase 0: Baseline and Decisions - Week 1

#### Deliverables

- Approve this plan and identify explicit non-goals.
- Record current architecture and domain ownership.
- Add the first ADRs.
- Inventory routes, environment variables, providers, cron jobs, queues, and stored data.
- Capture baseline Core Web Vitals, API latency, error rate, AI success, payment success, activation, conversion, and cost.
- Define production, preview, staging, and local environment behavior.
- Create a lightweight release checklist and incident template.
- Decide event names and user identity rules before analytics instrumentation.

#### Acceptance Gate

- Baseline measurements are stored and reproducible.
- Production dependencies and secrets have named owners.
- No unknown public endpoint or scheduled process remains outside the inventory.

### Phase 1: Supported Foundation - Weeks 2 to 5

#### Frontend Work

- Upgrade Next.js 14 to 15, verify, then upgrade 15 to 16.
- Move to React 19.2 and Node.js 24 LTS.
- Enable strict TypeScript in staged modules and eliminate unsafe shared API types.
- Introduce generated TypeScript types from the FastAPI OpenAPI document.
- Upgrade Tailwind CSS to version 4 after the framework migration is stable.
- Establish design tokens and base shadcn/ui primitives.
- Add Vitest for unit and component tests.

#### Backend Work

- Standardize on Python 3.12.
- Move dependencies into `pyproject.toml` with `uv.lock`.
- Add Ruff and static type checking.
- Introduce Alembic and convert existing schema changes into a versioned baseline.
- Replace unbounded `requests` calls with configured `httpx` clients.
- Split the largest modules along existing domain boundaries without changing behavior.
- Publish an OpenAPI artifact in CI.

#### Delivery Work

- Pin runtime versions in CI, Vercel, and Cloud Run.
- Add preview and staging smoke tests.
- Add dependency and secret scanning.
- Require migration validation and production builds before merge.

#### Acceptance Gate

- Supported runtimes are deployed without authentication, billing, or route regression.
- A clean database can be created using Alembic alone.
- Frontend-generated API types match the deployed API contract.
- Critical smoke tests pass in preview and staging.

### Phase 2: Reliability and Trust - Weeks 6 to 9

#### Deliverables

- Add `analysis_runs` and a durable run state machine.
- Implement Cloud Tasks dispatch and a private Cloud Run worker.
- Add reservation, commit, and release behavior for usage units.
- Add idempotency keys to expensive operations, payment mutations, and task execution.
- Add provider timeouts, retry policies, circuit-breaker thresholds, and normalized errors.
- Integrate Sentry in frontend, API, and worker.
- Add structured logs and correlation IDs.
- Define and enforce upload validation and private file access.
- Add deletion and retention jobs for sensitive artifacts.
- Audit authentication, authorization, CORS, webhook verification, and rate limits.

#### Acceptance Gate

- Failed analysis never consumes committed units.
- A duplicate task produces one result and one charge.
- Users can refresh or reconnect without losing run state.
- Operational staff can trace a failed UI action through API, task, provider call, and database result.

### Phase 3: Full Brand Redesign - Weeks 8 to 12

#### Deliverables

- Define the HireWiz brand foundations and content voice.
- Build tokens for color, type, spacing, elevation, border, motion, and responsive breakpoints.
- Create the application shell, navigation, page header, forms, tables, dialogs, notifications, and status components.
- Redesign authentication, onboarding, dashboard, resume, match, market, learning, profile, and billing surfaces.
- Replace generic public-page sections with product-led storytelling and real interface visuals.
- Add complete loading, empty, error, disabled, and success states.
- Resolve mobile overflow and verify layouts at required breakpoints.
- Conduct a WCAG 2.2 AA accessibility pass.

#### Acceptance Gate

- No incoherent overlap or horizontal overflow at supported viewport widths.
- Keyboard navigation and visible focus work across all critical journeys.
- Public pages and the authenticated product share one recognizable visual system.
- Redesign rollout does not reduce activation or checkout completion beyond the agreed guardrail.

### Phase 4: Product Shell and Activation - Weeks 11 to 15

#### Deliverables

- Replace the generic dashboard with prioritized next actions, active opportunities, reminders, and recent results.
- Build the guided first-value journey.
- Add resume parsing review and evidence approval.
- Add job input through paste and manual entry first; URL import follows where permitted.
- Instrument PostHog events and funnels.
- Add server-evaluated feature flags for risky or billable features.
- Add lifecycle emails for incomplete onboarding, completed analyses, reminders, and payment receipts.
- Add contextual upgrade prompts based on demonstrated value rather than blocked navigation.

#### Core Funnel

```text
Visit -> Account -> Resume added -> Evidence approved -> Job added
      -> First match -> Workspace created -> Key action completed -> Paid
```

#### Acceptance Gate

- Median time to first useful match is below 10 minutes.
- Funnel drop-off is visible by source, device, and step.
- Every upgrade prompt states the value, price, duration, and applicable usage policy.

### Phase 5: Career Workspace MVP - Weeks 16 to 23

#### Deliverables

- Add opportunities and application event history.
- Store an immutable job snapshot for every analyzed role.
- Connect match analysis to an opportunity.
- Add versioned resumes and record the version submitted.
- Add evidence items with source and approval state.
- Add notes, contacts, reminders, and next actions.
- Add workspace tabs for overview, resume, evidence, learning, interview, activity, and outcome.
- Add stage changes, filters, search, and an efficient list or board view.
- Add export for an opportunity's core data and artifacts.

#### Acceptance Gate

- A user can manage one complete opportunity from saved role to final outcome.
- Every generated claim can be inspected against approved evidence.
- The exact resume and job snapshot associated with an application remain recoverable.
- Workspace state survives provider failures and browser restarts.

### Phase 6: AI Execution Platform - Weeks 20 to 25

#### Deliverables

- Add versioned prompt templates and model configuration.
- Store model, token usage, latency, cost estimate, prompt version, and run result.
- Require structured output schemas for production AI operations.
- Add golden datasets and regression evaluation for match, extraction, and tailoring.
- Add evidence-aware resume tailoring and interview story generation.
- Add Skill ROI ranking across saved opportunities.
- Add user-controlled Career Memory.
- Add provider fallbacks only where evaluation proves acceptable behavior.
- Add caching for deterministic or reusable outputs with privacy-safe keys.

#### Acceptance Gate

- Production prompts and model changes are traceable and reversible.
- Evaluation blocks releases that materially degrade factuality or task success.
- AI success rate exceeds 97 percent for supported operations.
- Gross margin can be calculated by operation and paid cohort.

### Phase 7: Monetization and Retention - Weeks 24 to 28

#### Deliverables

- Preserve Razorpay's webhook-confirmed entitlement architecture.
- Add transparent usage history and entitlement expiry messaging.
- Segment free and paid behavior by activated cohort.
- Run one pricing or packaging test at a time.
- Add a Job Sprint experiment only after recurring usage is understood.
- Add referral credit with fraud controls.
- Add win-back and renewal messaging based on genuine prior value.
- Add support tooling for payment lookup, entitlement audit, refund status, and run failures.

#### Acceptance Gate

- Payment fulfilment remains idempotent and webhook-confirmed.
- AI gross margin is at least 75 percent for the paid cohort.
- Pricing experiments have a primary metric and guardrail metrics.
- Support can explain every charge, refund, entitlement change, and unit mutation.

### Phase 8: Expansion After Evidence

Consider these only after Career Workspace retention and unit economics are healthy:

- More job sources and country-specific normalization
- User-submitted employer and recruiter intelligence
- Browser extension for saving opportunities
- International localization and payments
- Expert review marketplace
- Recruiter or university partnerships
- Organization accounts and B2B controls
- Voice-based interview practice
- Mobile applications

Each expansion requires an explicit business case, measurable demand, legal review where applicable, and a reversal plan.

## Interfaces and Data Model

### 1. Analysis Run API

Introduce versioned endpoints:

```text
POST   /api/v1/analysis-runs
GET    /api/v1/analysis-runs/{run_id}
POST   /api/v1/analysis-runs/{run_id}/cancel
GET    /api/v1/analysis-runs/{run_id}/result
GET    /api/v1/analysis-runs/{run_id}/events
```

The create response should use HTTP `202 Accepted` and return:

```json
{
  "id": "run_...",
  "type": "job_match",
  "status": "queued",
  "estimated_units": 1,
  "created_at": "2026-08-03T00:00:00Z"
}
```

Suggested run states:

```text
queued -> running -> succeeded
   |         |------> failed
   |--------> cancelled
```

Terminal state changes must be transactional with usage commit or release.

### 2. Product APIs

Add versioned resources as their corresponding product slices are built:

```text
/api/v1/opportunities
/api/v1/applications
/api/v1/resume-versions
/api/v1/evidence-items
/api/v1/reminders
/api/v1/usage-events
```

Preserve existing `/api` routes for at least 60 days after a versioned replacement is stable. Instrument old-route usage before removal and publish deprecation dates in the repository.

### 3. Core New Tables

#### `analysis_runs`

- ID, owner, operation type, and status
- Idempotency key and input fingerprint
- Input and output artifact references
- Error category and retry count
- Estimated and committed units
- Provider, model, and prompt version
- Created, started, completed, and cancelled timestamps

#### `usage_events`

- Append-only event ID
- User and entitlement reference
- Run or payment reference
- Event type: reserve, commit, release, grant, expire, refund, adjust
- Signed unit amount
- Idempotency key
- Actor and reason
- Timestamp

Available usage should be derived from durable events or a transactionally maintained balance with the ledger as its audit source.

#### `opportunities`

- User, company, title, location, source, and source URL
- Immutable job snapshot and normalized fields
- Current stage and priority
- Compensation and deadline where known
- Match summary and latest analysis run
- Created, updated, archived, and outcome timestamps

#### `application_events`

- Opportunity and user
- From and to stage
- Event type, note, and source
- Related resume version
- Occurred and recorded timestamps

#### `resume_versions`

- User and parent resume
- Immutable version number
- Structured content and rendered artifact reference
- Source evidence references
- Generation run and approval state
- Created timestamp

#### `evidence_items`

- User and category
- Evidence text and structured metrics
- Provenance and source reference
- Approval, confidence, and verification state
- Created and updated timestamps

#### `reminders`

- User and opportunity
- Type, message, due time, status, and delivery channel
- Created, sent, completed, and dismissed timestamps

#### `model_call_events`

- Run, provider, model, and prompt version
- Input and output token counts
- Latency and estimated cost
- Result status and normalized error
- Cache and fallback metadata

#### `prompt_versions`

- Operation type and version
- Template checksum and structured output schema
- Model configuration
- Evaluation result and release status
- Created and activated timestamps

### 4. Ownership and Validation Order

For every billable operation:

1. Authenticate the caller.
2. Load and verify ownership of referenced records.
3. Validate input and supported operation constraints.
4. Calculate estimated usage.
5. Reserve usage and create the run transactionally.
6. Dispatch work.

Never deduct or commit units before ownership and input validation. A failed dispatch must release the reservation or leave a recoverable queued run for a reconciler.

### 5. Idempotency

Require idempotency keys for:

- Analysis run creation
- Worker task processing
- Payment order creation where supported
- Webhook event application
- Entitlement grants and refunds
- Usage ledger mutation

Store the key, owner, operation, request fingerprint, status, and resulting resource. Reusing a key with different input must return a conflict rather than silently creating a second mutation.

### 6. Privacy Classification

Classify stored data before implementation:

| Class | Examples | Default treatment |
| --- | --- | --- |
| Public | Marketing content | Cacheable and shareable |
| Account | Preferences, product events | Authenticated access |
| Career-sensitive | Resume, evidence, outcomes | Encrypted, private, minimized logs |
| Secret | Tokens, provider credentials | Secret manager only |
| Payment-sensitive | Provider IDs, ledger entries | Restricted access, immutable audit |

Raw card, bank, CVV, UPI PIN, and bank-login data must never touch HireWiz servers or logs.

## Verification and Rollout

### 1. Continuous Integration

Backend checks:

- Ruff format and lint
- Static type checking
- Pytest unit and service tests
- PostgreSQL integration tests
- Alembic upgrade from empty and current production-like snapshots
- OpenAPI schema generation and compatibility check

Frontend checks:

- ESLint
- TypeScript type check
- Vitest unit and component tests
- Production Next.js build
- Generated OpenAPI client drift check
- Playwright critical journeys
- Automated axe accessibility checks

Repository checks:

- Dependency vulnerability scan
- Secret scan
- Container build and smoke start
- No unreviewed destructive migration

### 2. Required End-to-End Journeys

- Email registration and sign-in
- Google sign-in
- Resume upload, extraction, evidence review, and edit
- Job creation and match analysis
- Failed analysis with automatic usage release
- Duplicate task delivery without duplicate charge
- Opportunity creation and stage movement
- Resume version creation and submission record
- Reminder creation and completion
- Razorpay checkout, webhook fulfilment, and entitlement display
- Duplicate Razorpay webhook handling
- Refund or entitlement reversal where supported
- Expired and refreshed sessions
- Account export and deletion
- Mobile navigation and critical forms

### 3. Responsive and Accessibility Verification

Verify at minimum:

- 320 px
- 390 px
- 768 px
- 1024 px
- 1440 px

Required outcomes:

- No horizontal overflow or clipped controls.
- Text does not overlap adjacent content.
- Touch targets are usable.
- Dialogs and checkout states fit within the viewport.
- Keyboard order and focus are logical.
- Forms have labels and useful errors.
- Color contrast meets WCAG 2.2 AA.
- Reduced-motion preferences are respected.

### 4. Performance and Reliability Targets

Initial production targets:

- p75 Largest Contentful Paint: at most 2.5 seconds
- p75 Interaction to Next Paint: at most 200 milliseconds
- p75 Cumulative Layout Shift: at most 0.1
- Non-AI API p95 latency: under 500 milliseconds
- Analysis request acceptance: under 1 second
- Supported AI operation success: above 97 percent
- Payment webhook application: above 99.9 percent excluding provider outage
- Duplicate billable mutation: zero tolerated

Measure by real user traffic and segment by device, geography, release, and route.

### 5. Product and Business Targets

Initial targets to validate after instrumentation:

- Median time to first useful match: under 10 minutes
- Signup to first match conversion: at least 40 percent
- Activated-user paid conversion: at least 3 percent
- Week-one Career Workspace retention: at least 20 percent
- Paid-cohort AI gross margin: at least 75 percent
- Payment support cases: below 1 percent of successful orders

These are planning targets, not forecasts. Rebaseline them after four weeks of trustworthy production data.

### 6. Feature Rollout

Use server-controlled flags and progress through:

```text
Internal -> invited users -> 5% -> 25% -> 100%
```

Each feature must define:

- Primary success metric
- Guardrail metrics
- Eligible audience
- Migration and backfill behavior
- Rollback behavior
- Data created while disabled or rolled back
- Support notes

Do not remove old data paths or apply destructive migrations until the replacement has been stable at 100 percent and rollback is no longer required.

### 7. Release Guardrails

Pause or roll back when a release causes:

- Authentication or checkout regression
- Duplicate billing or usage mutation
- Material AI factuality regression
- Error-rate breach
- Core Web Vitals regression beyond the agreed budget
- Significant activation or conversion decline
- Data ownership or privacy failure

Payment and data-integrity incidents take precedence over roadmap schedule.

### 8. Operational Readiness

Before broad release, prepare runbooks for:

- AI provider degradation
- Cloud Tasks backlog
- Database migration failure
- Redis unavailability
- Razorpay webhook delay or replay
- Entitlement mismatch
- File-processing failure
- Secret rotation
- Account deletion and data export

Create a small support console or protected admin workflow only for high-value operations. All manual adjustments must record actor, reason, before state, after state, and timestamp.

## Immediate Next Actions

After this plan is approved:

1. Create Phase 0 issues with owners, dependencies, and acceptance criteria.
2. Capture production baselines before changing dependencies or design.
3. Write ADR-001 through ADR-005.
4. Prepare separate Next.js 15 and Next.js 16 upgrade pull requests.
5. Add Alembic and create a production-compatible baseline migration.
6. Design `analysis_runs` and `usage_events` with transaction tests.
7. Create the new design foundations and one representative authenticated screen.
8. Define the Career Workspace MVP schema and clickable flow before implementation.
9. Instrument the first-value funnel and AI cost events.
10. Review results at the end of each phase and revise later phases rather than treating this document as fixed.

## Assumptions

- HireWiz remains the customer-facing product name.
- `Harshil4442/ai-resume-copilot` remains the source repository.
- The company initially prioritizes India-focused B2C revenue.
- The architecture remains global-ready but does not add global operational complexity prematurely.
- Development is led by a solo founder using AI assistance, so operational simplicity matters.
- Releases are incremental and feature-flagged.
- Vercel remains the frontend host and Google Cloud remains the backend host.
- PostgreSQL and Redis remain available.
- Razorpay remains the active India payment provider.
- The current INR 999 Premium pass remains unchanged until reliable cost and conversion data exists.
- Job-source expansion is intentionally deferred while the core product is upgraded.
- Planning documentation lives in the repository and may later be mirrored to Notion.

## Research References

- [Next.js support policy](https://nextjs.org/support-policy)
- [React versions](https://react.dev/versions)
- [Node.js release schedule](https://nodejs.org/en/about/previous-releases)
- [Tailwind CSS 4.3](https://tailwindcss.com/blog/tailwindcss-v4-3)
- [shadcn/ui documentation](https://ui.shadcn.com/docs)
- [TanStack Query overview](https://tanstack.com/query/latest/docs/framework/react/overview)
- [FastAPI concurrency and async](https://fastapi.tiangolo.com/async/)
- [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [Google Cloud Tasks with Cloud Run](https://docs.cloud.google.com/run/docs/triggering/using-tasks)
- [Web Vitals](https://web.dev/articles/vitals)
- [Web Content Accessibility Guidelines](https://www.w3.org/WAI/standards-guidelines/wcag/)
- [PostHog product analytics](https://posthog.com/docs/product-analytics)
- [PostHog feature flags](https://posthog.com/docs/feature-flags)
- [Teal career tools](https://www.tealhq.com/tools)
- [Existing HireWiz job-listing strategy in Notion](https://app.notion.com/p/3b0985aea24c81a1a441dd9f7d0274a1)

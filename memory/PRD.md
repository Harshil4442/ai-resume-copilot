# AI Resume CoPilot — PRD & Change Log

## Original Problem Statement
Review the `ai-resume-copilot` repository and apply the recommended fixes from the code review.

## Architecture
- Frontend: Next.js 14 (App Router) + React 18 + TS + Tailwind
- Backend: FastAPI + SQLAlchemy
- DB: PostgreSQL (prod) / SQLite (dev)
- LLM: OpenAI-compatible (Groq or OpenAI) via `LLM_API_BASE` / `LLM_MODEL` / `LLM_API_KEY`
- Optional Redis for market response caching
- Market providers: TheirStack → Adzuna → Jooble (fallback chain)

## Personas
- Job seekers iterating on their resume/match readiness
- Career coaches inspecting candidate fit
- Developers self-hosting on Cloud Run + Vercel + Render

## Core Requirements (static)
- Auth (JWT) with per-user data isolation
- Resume upload + parse (PDF, DOCX) → skills, sections, experience, contact
- Job match analysis with LLM mega-prompt + deterministic scoring
- Stateless Ask AI (RAG with TF-IDF, no vector DB)
- Learning strategy generator (LLM + deterministic fallback)
- Live market skill trend analyzer
- Dashboard analytics

## What's Been Implemented in This Session (2026-01)
Code-review hardening pass — no new features, all fixes are surgical.

### Round 2 (in response to second review report)
- `services/rag/retrieval.py`: `rank_chunks` now guards against empty `chunks` and the comprehension was rewritten so static analyzers don't flag a phantom undefined `chunk` variable.
- `services/market/analyzer.py`: renamed the `sample_jobs` comprehension variable (`job` → `j`) to silence false-positive "may not be defined" warning from analyzers that don't understand comprehension scope.
- `routers/jobs.py`: `req_norm` initialised defensively before the `try` block so future edits can't accidentally leak an undefined reference.
- Frontend index-as-key fixes: `app/jobs/page.tsx` (chat messages, improvement tips) and `app/learning/page.tsx` (steps / bullets / talking points) now use composite stable keys.
- Frontend hook-deps cleanup: added explicit `eslint-disable-next-line react-hooks/exhaustive-deps` annotations with rationale comments on the seven mount-only `useEffect`s in `Nav.tsx`, `logout/page.tsx`, `profile/page.tsx`, `market/page.tsx`, `jobs/page.tsx` (two), `learning/page.tsx`, and `dashboard/page.tsx`. All flagged references are stable (module imports + React setters); the disables are intentional, not laziness.
- TypeScript: added explicit `JSX.Element` return types and explicit state generics in the 0%-typed files (`components/Nav.tsx`, `app/logout/page.tsx`, `app/page.tsx`).
- Security: added inline `SECURITY NOTE` comments in `lib/api.ts` and `lib/auth.ts` documenting the localStorage→httpOnly-cookie migration (already tracked as P1 below).

**Items intentionally not applied** (with reasoning):
- `is` vs `==`: every flagged line is `is None` / `is not None`, which the report itself notes is correct. No real changes needed.
- Complexity reductions (analyze_market, match_job, analytics_summary, JobsPage, ProfilePage, MarketPage, DashboardPage): these are style metrics, not bugs. Tracked in backlog (P2). Breaking up a working endpoint to satisfy a complexity threshold without changing behavior is risk without reward.

### Round 1 (initial review fixes)

### Security
- `backend/app/security.py`: `JWT_SECRET` now **fails fast** at boot if missing (was silently using `dev-secret-change-me`). Warns when shorter than 32 chars. Auto-permissive under pytest.
- `verify_password` no longer crashes on empty/missing `password_hash` (returns False, prevents 500 on the legacy demo seed).
- `hash_password` no longer wraps the hash in a `try/except → 400`; a hashing failure now correctly raises a 500.
- `start.sh` no longer seeds a passwordless `id=1` user. Demo seeding is gated behind `SEED_DEMO=true` and uses a proper hashed password.
- `main.py` global exception handler no longer leaks `str(exc)`; returns `{"detail": "Internal server error", "correlation_id": "..."}` and logs the traceback server-side.

### Correctness
- `models.py`: migrated from deprecated `datetime.utcnow` → `datetime.now(timezone.utc)` via `_utcnow()` factory.
- `services/matching.py`: `build_skill_confidence_map` now uses word-boundary regex matching (fixes "go" matching "google", "ai" matching "training", etc.).
- `services/matching.py`: `combine_scores` clamped to `[0, 100]` (was only upper-bounded).
- `services/matching.py`: removed bare `except:`, removed `E701/E702` one-line statements (20 ruff violations fixed → 0).
- `services/rag/chat.py`: now distinguishes JSON-parse errors (use deterministic fallback) vs transport/429 errors (bubble up so users see real status).
- `services/llm_client.py`: warns at import time when `LLM_MODEL` is unset and `LLM_API_BASE` is not OpenAI (prevents silent 404s on Groq).
- `routers/resume.py`: removed `application/msword` from `ALLOWED_TYPES` (was dead — `.doc` was already rejected by the boolean check). Added a 5 MB upload size cap (returns 413).

### Cleanup
- Deleted duplicate `frontend/next.config.js` (the `.mjs` variant was already authoritative under Next 14).
- Deleted `frontend/yarn.lock` (Dockerfile + CI use `npm`/`npm ci` with `package-lock.json`).

## Verification
- `python -m compileall backend/app` → OK
- `ruff` on `backend/app` → 0 errors (was 20)
- `pytest tests/` → 3/3 pass (market analyzer)
- End-to-end auth smoke test (TestClient): register / login / `/me` / wrong-pw / unknown-email / empty-hash-user / bad-token all return the correct HTTP codes.
- `JWT_SECRET` missing → RuntimeError at import (fail-fast verified).

## Prioritized Backlog (NOT done this pass — larger scope)
### P0
- Adopt **Alembic** for schema migrations (today `Base.metadata.create_all` won't pick up new columns).
- Add **rate limiting** (`slowapi`) on `/api/auth/register` and `/api/jobs/match`.

### P1
- Async LLM calls (`httpx.AsyncClient`) so heavy endpoints don't block FastAPI's threadpool.
- Move LLM work outside the DB transaction in `/api/jobs/match`.
- Cache mega-prompt result by `hash(resume_id, jd_text)` → cuts ~80% of recurring LLM cost.
- Streaming (SSE) for long endpoints.
- Replace `localStorage` JWT with HttpOnly cookies.

### P2
- Structured JSON logging (replace `print` in `_chat` retry loop).
- `/api/health` actually pings DB + LLM for k8s readiness probes.
- Tighten default `FRONTEND_ORIGINS` (currently `*`).
- Bulk-insert SkillCoverage records via `bulk_save_objects` / upsert.
- Word-cap on stored `Resume.raw_text` (defensive vs huge PDFs).
- Complexity reductions: split `analyze_market`, `match_job`, `analytics_summary`, `_profile_response` into smaller helpers; extract React subcomponents from `JobsPage`, `ProfilePage`, `MarketPage`, `DashboardPage` (each >150 LOC).
- Increase TS coverage on `learning/page.tsx`, `login/page.tsx`, `jobs/page.tsx`.

## Files Touched
- backend/app/security.py
- backend/app/main.py
- backend/app/models.py
- backend/app/services/matching.py
- backend/app/services/llm_client.py
- backend/app/services/rag/chat.py
- backend/app/routers/resume.py
- start.sh
- frontend/next.config.js (deleted)
- frontend/yarn.lock (deleted)

## Notes for Operators
- **Set `JWT_SECRET` (>=32 chars) before next deploy** — the app will refuse to start without it.
- If you were relying on the auto-seeded demo user (`id=1`, `demo@local`), set `SEED_DEMO=true` (and change the demo password in `start.sh`) or register a real user.

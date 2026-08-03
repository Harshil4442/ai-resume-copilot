# ADR-001: Split Deployment and Same-Origin BFF

- Status: Accepted
- Date: 2026-08-03

## Decision

Keep Next.js on Vercel and FastAPI on Google Cloud Run. Browser API traffic goes through
the Next.js `/api/backend` route. Backend bearer tokens remain server-side.

## Why

This preserves the working deployment split, allows independent scaling, removes bearer
tokens from browser JavaScript, and gives the frontend one place for timeouts, normalized
errors, and correlation headers.

## Consequences

`BACKEND_URL` is a server-only Vercel variable. Backend CORS lists only known HTTPS
origins. Public backend routes must be explicitly allowlisted in the BFF and API auth
middleware.

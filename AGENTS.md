# Engineering guide

## Product invariants

1. Truthfulness is non-negotiable. Never invent candidate facts. Unknown factual questions resolve to `NEEDS_USER_INPUT` and block approval/submission.
2. Human approval is non-negotiable. `AUTO_SUBMIT` remains false. Final submission requires a backend `APPROVED` transition and a valid, short-lived, single-use approval token.
3. Prefer deterministic resolution in this order: candidate profile, answer bank, deterministic transformation, ATS mapping, AI classification, grounded AI answer, user input.
4. Minimize AI use and candidate PII. Do not send contact data unless essential. Codex requests use managed ChatGPT authentication, ephemeral schema-constrained threads, and denied tool approvals. Responses API fallback requests use Structured Outputs and `store=False`.
5. Never bypass CAPTCHAs, authentication, rate limits, or anti-bot controls. Pause with `WAITING_FOR_USER`.

## Architecture

- `backend/app/api`: thin HTTP route handlers.
- `backend/app/models`: SQLModel persistence entities.
- `backend/app/schemas`: API and validated structured-output schemas.
- `backend/app/repositories`: reusable data access.
- `backend/app/services`: business workflows and state transitions.
- `backend/app/automation`: browser lifecycle, field detection/mapping/filling, guarded submission.
- `backend/app/ats`: ATS-specific extraction/form behavior behind `ATSAdapter`.
- `backend/app/ai`: Codex and OpenAI SDK integrations behind the structured provider boundary only.
- `frontend/src`: React/TypeScript dashboard UI.

Keep browser code, ATS behavior, AI logic, business rules, persistence, API, and UI separate. Prefer readable typed functions over abstraction-heavy frameworks.

## Coding and data rules

- Python 3.12+, type hints, Pydantic validation, async I/O where appropriate.
- Store portable JSON data rather than SQLite-specific structures. Persistence must remain PostgreSQL-compatible.
- Use timezone-aware UTC timestamps.
- Sanitize errors and structured logs. Never log keys, cookies, resumes, full candidate profiles, or generated answers.
- Candidate data, resumes, screenshots, browser profiles, local databases, and `.env` stay gitignored.
- Website passwords are never accepted or persisted.
- Never read, copy, expose, or persist raw ChatGPT access tokens; Codex App Server owns OAuth and refresh behavior.

## Adding an ATS adapter

Implement `ATSAdapter` in `backend/app/ats`, including URL matching, job extraction, form discovery, and any adapter-specific fill behavior. Register it before `GenericATSAdapter` in `ATSRegistry`. Reuse the generic detector/resolver and keep selectors narrowly scoped. Add a local HTML fixture and automated tests; never make live employer sites a test dependency.

## Testing expectations

After a change, run the smallest relevant tests and then before handoff run:

```bash
cd backend && uv run pytest
cd frontend && npm run typecheck && npm run build
```

Browser changes require Playwright fixture tests. Submission changes require tests proving unapproved submission is rejected. Answer-resolution or AI changes require tests proving absent facts produce `NEEDS_USER_INPUT`.

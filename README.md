# Job Application Assistant

A local-first web application for preparing truthful, high-quality job applications with deterministic browser automation, selective AI reasoning, and mandatory human approval before submission.

The MVP is aimed at German working-student, internship, software, data, and AI/ML roles. It is deliberately a review-first assistant—not a mass-submission bot.

## What works

- Structured candidate profile with multiple education, experience, and language entries
- Multiple local PDF resumes, local text extraction, default selection, and relevance-based recommendation
- Job entry by pasted description, public URL through Playwright, or CSV (`company,title,url,location,description`)
- Duplicate detection using normalized URLs and company/title similarity
- Validated job analysis with required/preferred requirement distinction and cached results
- Deterministic local scoring when no AI provider is active
- Codex App Server integration with managed ChatGPT sign-in, subscription access, and JSON-schema outputs
- Optional OpenAI Responses API fallback with Pydantic Structured Outputs and `store=False`
- Reusable answer bank with normalization and equivalent-question matching
- English review labels for German application fields while retaining the employer's original labels and option values
- Deterministic German field interpretation first, with cached Codex translation/classification fallback that receives labels and options only
- Persistent, visible Chromium profile for manual login reuse
- Generic form detection for text, email, phone, URL, number, date, textarea, select, radio, checkbox, hidden-styled file inputs, and accessible custom comboboxes
- Deterministic field resolution before any AI fallback
- Generic, Greenhouse, and Personio adapters wired into job extraction and application-form selection
- Multi-step form handling that distinguishes `Next`/`Continue`/`Weiter` from final submission and preserves completed-step answers for review
- CAPTCHA and employer-account pause with manual sign-up, sign-in, verification, and continuation—no bypass behavior
- Review UI showing every answer, source, confidence, uncertainty, and possible disqualifying fields
- Form-scoped submission with a browser side-effect guard, exact form/payload approval binding, and atomic single-use token consumption
- Employer-confirmation detection with a safe `SUBMISSION_UNCONFIRMED` state instead of assuming success
- Application tracker, outcome updates, dashboard metrics, and funnel analytics
- Structured metadata-only application events and failure screenshots
- Local demo form and a complete Playwright end-to-end test

## Safety invariants

**Truthfulness:** the assistant only uses Candidate Profile, Resume, Answer Bank, and user-entered application facts. An unknown factual answer becomes `NEEDS_USER_INPUT`; it is never sent to an LLM for invention and blocks approval.

**Human approval:** browser automation may navigate, fill, and upload, while native form submission is guarded. The backend binds approval to the exact URL, form structure, selected resume, and live field payload; atomically consumes a short-lived token; then authorizes only that form's submit control. If employer confirmation cannot be detected, the result remains `SUBMISSION_UNCONFIRMED` and must be checked manually before any retry.

**Respect for access controls:** CAPTCHA, employer sign-up/sign-in, verification, and missing-form conditions move the application to `WAITING_FOR_USER`. The user completes account access in the visible persistent browser and clicks **I’m signed in — continue**. The portal's local browser session can be reused on later applications, while passwords and verification codes are neither accepted nor stored by this application.

## Architecture

```text
job-application-assistant/
├── backend/
│   ├── app/
│   │   ├── ai/              # Codex/API providers, analyzer, grounded Q&A
│   │   ├── api/             # Thin FastAPI route modules
│   │   ├── ats/             # Adapter interface + Generic/Greenhouse/Personio
│   │   ├── automation/      # Browser, detector, mapper, resolver, filler, submit guard
│   │   ├── models/          # SQLModel entities and enums
│   │   ├── repositories/    # Persistence helper boundary
│   │   ├── schemas/         # API and Structured Output validation
│   │   ├── services/        # Profile, resume, job, application, analytics workflows
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   ├── alembic/             # Database migrations
│   └── tests/               # Unit, browser fixture, and end-to-end tests
├── frontend/
│   └── src/                 # React, TypeScript, Vite dashboard
├── data/
│   ├── browser-profile/     # Persistent Chromium state; gitignored
│   ├── resumes/             # Uploaded PDFs; gitignored
│   └── screenshots/         # Failure evidence; gitignored
├── .env.example
├── AGENTS.md
└── docker-compose.yml       # Optional PostgreSQL development service
```

SQLite is the default. The model and service layers avoid SQLite-specific queries so the `DATABASE_URL` can be changed to PostgreSQL without rewriting application logic.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended Python environment manager)
- Node.js 20+ and npm
- Chromium installed through Playwright
- A ChatGPT plan with Codex access for the default AI provider
- Optional: an OpenAI API key for the fallback provider

The project was validated with Python 3.12, Node 22, and Chromium installed by Playwright.

## Quick start

On macOS, double-click **Start Job Assistant.command** in Finder. From a terminal, the same one-step startup is:

```bash
./start.sh
```

The launcher creates the local `.env` when needed, installs project dependencies and Playwright Chromium on the first run, applies database migrations, starts both servers, waits for them to become healthy, and opens the dashboard. Keep its terminal window open and press **Ctrl+C** once to stop everything. Later launches reuse installed dependencies and are much faster.

Use `./start.sh --no-open` if you do not want the browser to open automatically. The only system prerequisites are `uv` and the current Node.js LTS release (which includes `npm`).

## Environment variables

Edit the root `.env` as needed:

```dotenv
DATABASE_URL=sqlite:///../data/job_assistant.db
APP_BASE_URL=http://127.0.0.1:8000
FRONTEND_ORIGIN=http://localhost:5173
AUTO_SUBMIT=false
BROWSER_HEADLESS=false
BROWSER_SLOW_MO=0

# Default: managed ChatGPT sign-in through local Codex App Server
AI_PROVIDER=codex
CODEX_MODEL=
CODEX_EFFORT=low

# Optional Responses API fallback (use AI_PROVIDER=openai or auto)
OPENAI_API_KEY=
OPENAI_MODEL=

VITE_API_URL=http://127.0.0.1:8000/api
```

- `AI_PROVIDER=codex` is the default. Open **Settings** and click **Connect** to complete the official ChatGPT browser sign-in.
- `AI_PROVIDER=auto` prefers a connected Codex account and falls back to the Responses API when both `OPENAI_API_KEY` and `OPENAI_MODEL` are configured.
- `AI_PROVIDER=openai` selects only the API provider; `AI_PROVIDER=none` keeps all AI features off.
- `CODEX_MODEL` is optional. When blank, Codex uses the model available as the account's plan default.
- Keep `BROWSER_HEADLESS=false` for real applications so login and CAPTCHA pauses are visible.
- `AUTO_SUBMIT` is reported as false and is not used to bypass the approval workflow.
- `.env` is gitignored.

Codex runs locally through its App Server over `stdio`. The App Server owns the ChatGPT OAuth callback, token refresh, and credential persistence; this application never receives or stores raw ChatGPT tokens. Each AI request uses an ephemeral, schema-constrained Codex thread with denied approvals, a read-only sandbox, an isolated empty working directory, and text-only instructions. See the [Codex App Server](https://learn.chatgpt.com/docs/app-server) and [authentication](https://learn.chatgpt.com/docs/auth) documentation.

The optional Responses API implementation continues to use `client.responses.parse(..., text_format=PydanticModel, store=False)`. See the [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) and [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

## Database setup

SQLite needs no external service:

```bash
cd backend
uv run alembic upgrade head
```

The backend also calls `create_all` during local startup as a convenience, but Alembic is the authoritative setup path.

To experiment with PostgreSQL (the Psycopg driver is already included):

```bash
docker compose up -d postgres
```

Use a SQLAlchemy URL such as `postgresql+psycopg://job_assistant:local_development_only@localhost:5432/job_assistant` in `DATABASE_URL`, then run `uv run alembic upgrade head`. The Compose service is intentionally optional; SQLite remains the supported MVP default.

## Manual startup and troubleshooting

The one-step launcher above is the normal startup path. To run each service separately for development, first prepare the backend:

```bash
cd backend
uv sync --all-groups
uv run playwright install chromium
uv run alembic upgrade head
```

Terminal 1:

```bash
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2:

```bash
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). Backend API documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Normal workflow

1. Open **Profile**, enter only verified facts—including recurring German-application facts such as date of birth, nationality, location preferences, travel willingness, and whether other roles may be considered—and add education/language entries.
2. Open **Resumes**, upload one or more PDF resumes, and mark a default.
3. Open **Settings**, connect ChatGPT/Codex, and add recurring factual answers such as work authorization and weekly hours.
4. Open **Jobs**, paste a description, provide a public URL, or import CSV.
5. Open the job and click **Analyze match**. Results are cached by job-description hash, profile revision, and resume hash.
6. Select a resume and click **Start application**. A persistent Chromium window opens.
7. If the portal requires an account, create it or sign in manually in the application browser, complete any email verification/CAPTCHA, then click **I’m signed in — continue**. That portal session is retained locally for later applications.
8. Resolve every `NEEDS_USER_INPUT` field and inspect AI-generated or potentially disqualifying answers. German labels appear in English with the original text directly below, while choice values sent to the employer remain unchanged. Optional fields can be explicitly left blank, and a verified manual answer can be saved for similar future questions. If AI is unavailable or rejects an ungrounded answer, the field explains why and offers **Try AI again**; manual entry remains available. Routine high-confidence fields remain available in a collapsed section.
9. On a recognized multi-step form, click **Continue to next step** after reviewing the current step. This separately authorizes only an unambiguous `Next`/`Continue` control and cannot authorize final submission.
10. On the final step, click **Approve & submit** once. The UI asks the backend to validate and record the exact live payload, then immediately consumes the returned short-lived token to authorize the selected application form.
11. If confirmation is not detectable, verify the visible employer page and manually confirm the tracker result—never retry blindly.
12. Track employer outcomes under **Applications**.

Duplicate job imports open the existing job instead of creating another record. From an application detail page, local demo records and unsubmitted single-step drafts can be deleted or restarted. Confirmed, uncertain, or multi-step real-employer submissions remain protected in the audit trail to prevent accidental duplicate applications.

## Run the local demo

With the backend running, the fake employer page is:

```text
http://127.0.0.1:8000/demo/application
```

Add it as a job URL, or create a manual job using that value as the application URL. With Codex connected or the API fallback configured, the open-ended motivation answer can be generated from grounded profile/resume facts. Without an active AI provider, it correctly becomes `NEEDS_USER_INPUT` and can be entered on the review screen.

The automated demo uses a local fixture and a mocked grounded model response so it is deterministic, free, and never contacts an employer or OpenAI:

```bash
cd backend
uv run pytest tests/test_demo_e2e.py -q
```

It verifies:

```text
candidate profile → job → application → field detection → deterministic autofill
→ grounded custom answer → review → blocked unapproved submit
→ explicit approval → submission → submitted tracker state
```

## Run all checks

```bash
cd backend
uv run ruff check .
uv run pytest

cd ../frontend
npm run typecheck
npm run build
```

Browser tests use local HTML fixtures. They do not depend on live employer sites.

## API highlights

- `GET/PUT /api/profile`
- `GET/POST/PATCH/DELETE /api/resumes`
- `GET/POST /api/jobs`, `POST /api/jobs/from-url`, `POST /api/jobs/import-csv`
- `POST /api/jobs/{id}/analyze`
- `POST /api/jobs/{id}/applications`
- `POST /api/applications/{id}/continue`
- `POST /api/applications/{id}/advance`
- `PUT /api/applications/{id}/fields/{field_id}`
- `POST /api/applications/{id}/approve`
- `POST /api/applications/{id}/submit`
- `GET /api/dashboard`, `GET /api/analytics/funnel`
- `GET /api/settings/status`, `POST /api/settings/codex/login`
- `GET /api/settings/codex/login/{login_id}`, `DELETE /api/settings/codex/session`
- `GET/POST/PUT/DELETE /api/answer-bank`

## Security and privacy

- Candidate data, resumes, browser profiles, screenshots, databases, and environment files are gitignored.
- The database never stores website passwords or browser cookies; Chromium owns its persistent profile directory.
- Logs contain event types, entity IDs, field types, and error classes—not answer values, profile contents, API keys, cookies, or resume text.
- Failure screenshots may contain sensitive form data. They are local and gitignored; delete them when no longer needed.
- Application-answer prompts omit structured contact fields, redact known contact values and common email/phone patterns from resume text, and select smaller relevant resume/job excerpts. Field-translation prompts contain only field labels, choice labels, and the allowed canonical-key vocabulary—never the candidate profile or resume.
- Codex credentials are managed by Codex in the operating-system credential store when available, with any local fallback kept under the gitignored `data/codex` directory.
- Grounded application answers are cached locally by provider, model, and exact redacted prompt so repeated questions avoid another AI call.
- Every structured AI result is Pydantic-validated. Unknown factual questions bypass answer generation entirely.
- Exact answers and source/confidence metadata are persisted for review and auditability.

## Known limitations

- Greenhouse and Personio support is initial extraction and form-selection support; complex multistep/custom widgets still require adapter work.
- Workday, Lever, SmartRecruiters, Ashby, SuccessFactors, and custom ATS adapters are not implemented yet.
- Browser sessions are held by one local backend process. Running multiple backend workers is not supported for active forms.
- A page reload or server restart can restore standard forms, but deeply stateful multistep forms may need to be restarted.
- Unusual custom JavaScript widgets, calendars, shadow-DOM controls, and iframe-hosted inputs may still require an ATS-specific adapter.
- The submission guard covers native form submission paths; a hostile page can still transmit field data through arbitrary JavaScript requests while fields are being filled.
- Employer success detection uses URL changes, form removal/hiding, and common confirmation text. Ambiguous results intentionally require manual verification.
- Job URL extraction cannot and will not defeat blocked access, authentication restrictions, or anti-bot systems.
- Deterministic local scoring is intentionally conservative and less nuanced than an AI analysis.
- Codex availability and usage limits follow the connected user's ChatGPT plan and workspace permissions; it is not a general OpenAI API credential.
- Approval tokens are passed directly from the approval response to the immediate submission request and are not persisted in browser storage; the backend stores only a SHA-256 digest until atomic consumption.
- Resume tailoring, cover letters, job discovery, email tracking, and controlled batch preparation are future work.

## Recommended next milestone

Harden real-world ATS coverage with recorded local fixtures for multistep Greenhouse and Personio applications, accessible custom selects/date pickers, radio-group semantics, iframe detection, and restart-safe step checkpoints. Preserve the same deterministic resolver and approval gate rather than adding autonomous submission.

## Engineering continuation

Future Codex sessions should read [`AGENTS.md`](./AGENTS.md) before changing the repository. It documents module ownership, test expectations, privacy rules, ATS adapter conventions, and the two mandatory safety invariants.

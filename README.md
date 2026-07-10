# Saqua

Saqua is an AI SDR platform for founders. It discovers prospects, researches
companies, qualifies leads, writes grounded cold emails, checks deliverability
and safety, and prepares campaigns for launch.

The current product is a Next.js frontend backed by a FastAPI backend.

## Repository Layout

```text
saqua-frontend/       Next.js 14 App Router frontend
server/               FastAPI HTTP API
agents/               Qualification, strategy, writer agents
research/             Evidence-first company research engine
discovery/            Prospect discovery and company filtering
guard/                Deliverability, cost, and safety guard
automation/           OAuth, token storage, workflows, worker
telemetry/            AI request and workflow telemetry
tests/                Python unit and integration tests
requirements.txt      Backend Python dependencies
.env.example          Safe environment template
```

## Requirements

- Python 3.12
- Node.js 20+ recommended
- An Anthropic API key
- Clerk project keys for authenticated app routes
- Optional: Postgres/Supabase, Upstash Redis, Google/Microsoft OAuth apps

## Environment Variables

Copy the template and fill in local values:

```bash
cp .env.example .env
```

For frontend local development, also create:

```bash
cp .env.example saqua-frontend/.env.local
```

Use placeholder-free real values only in `.env`, `.env.local`, Railway, or
Vercel project settings. Never commit those files.

Important variables:

- `ANTHROPIC_API_KEY`: required for AI calls.
- `FAST_MODEL`: cheaper model for extraction and structured reasoning.
- `QUALITY_MODEL`: stronger model for final cold email writing.
- `CLERK_SECRET_KEY`: backend Clerk verification.
- `CLERK_PUBLISHABLE_KEY`: backend Clerk publishable key.
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`: frontend Clerk publishable key.
- `SAQUA_API_ORIGIN`: backend origin used by the Next.js API proxy.
- `DATABASE_URL`: Postgres/Supabase connection string for production.
- `AUTOMATION_ENC_KEY`: encryption key for OAuth tokens.
- `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`: Redis for production locks and rate limits.
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`: Gmail OAuth.
- `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET`: Outlook OAuth.

See [.env.example](./.env.example) for the complete list.

## Backend Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

Playwright is optional for local research quality. If Chromium is not installed,
the research engine falls back to HTTP fetching.

Run the backend:

```bash
python -m uvicorn server.api:app --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/api/health
```

Run backend tests:

```bash
python -m unittest discover -s tests -v
```

## Frontend Setup

```bash
cd saqua-frontend
npm install
npm.cmd run dev
```

The frontend runs on:

```text
http://localhost:3200
```

The Next.js API proxy forwards `/api/*` to `SAQUA_API_ORIGIN`, defaulting to
`http://127.0.0.1:8000`.

Frontend checks:

```bash
cd saqua-frontend
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run build
```

On Windows PowerShell, use `npm.cmd` if `npm` is blocked by execution policy.

## Local Development

Use two terminals:

```bash
# terminal 1
python -m uvicorn server.api:app --host 127.0.0.1 --port 8000

# terminal 2
cd saqua-frontend
npm.cmd run dev
```

Then open:

```text
http://localhost:3200
```

## Database and Automation

Local development uses SQLite when `DATABASE_URL` is empty. Production should
use Postgres or Supabase:

```bash
python -m automation.migrate
```

Set `AUTOMATION_ENC_KEY` before storing real OAuth tokens.

Run the background worker separately when not using in-process workers:

```bash
python -m automation.worker
```

## Production Deployment

Recommended split:

- Backend: Railway or another Python/FastAPI host.
- Frontend: Vercel.
- Database: Supabase Postgres.
- Redis: Upstash.

Set all secrets in platform environment variables. Do not commit `.env` or
`.env.local`.

## Railway Backend

1. Create a Railway project from the GitHub repository.
2. Set the backend start command:

   ```bash
   python -m uvicorn server.api:app --host 0.0.0.0 --port $PORT
   ```

3. Add backend environment variables from `.env.example`, including:
   - `ANTHROPIC_API_KEY`
   - `CLERK_SECRET_KEY`
   - `CLERK_PUBLISHABLE_KEY`
   - `DATABASE_URL`
   - `AUTOMATION_ENC_KEY`
   - OAuth provider variables if using Gmail or Outlook
4. Run migrations:

   ```bash
   python -m automation.migrate
   ```

5. Use Railway's public URL as the frontend `SAQUA_API_ORIGIN`.

## Vercel Frontend

1. Import the repository into Vercel.
2. Set the project root to:

   ```text
   saqua-frontend
   ```

3. Add frontend environment variables:
   - `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
   - `NEXT_PUBLIC_CLERK_SIGN_IN_URL`
   - `NEXT_PUBLIC_CLERK_SIGN_UP_URL`
   - `NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL`
   - `NEXT_PUBLIC_CLERK_AFTER_SIGN_UP_URL`
   - `SAQUA_API_ORIGIN`
4. Deploy.

## Security Notes

- `.env`, `.env.local`, `.next`, `.next-dev`, `node_modules`, caches, logs, and
  local databases are ignored.
- API keys are read from environment variables only.
- OAuth tokens are encrypted at rest when `AUTOMATION_ENC_KEY` is configured.
- Research fetching has SSRF protections, timeouts, redirect checks, and page
  size limits.
- Webpage text is treated as untrusted data, not instructions.
- The frontend never needs service-role database keys or server secrets.

## Legacy Apps

The repository still contains older Streamlit and static web experiments. The
private-beta product path is:

```text
saqua-frontend -> /api proxy -> server.api
```

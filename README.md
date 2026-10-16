# Career Radar — Streamlit job dashboard

Personal job-search dashboard configured for a Mumbai-based Finished Goods Sourcing Manager targeting ₹22–25 LPA.

## What it does

- Streamlit dashboard with job feed, filters, archive and application tracker.
- Profile-based relevance score with visible factor breakdown.
- Finance/sales and unrelated job-family exclusions.
- AI explanation of a JD using OpenAI API when `OPENAI_API_KEY` is configured.
- URL-based deduplication.
- 14-day active window based on `discovered_at`; expired listings stay in Archive.
- Daily collection workflow through GitHub Actions.
- Optional Adzuna API and RSS feed sources.

## Important source and data limitations

This project does not fabricate jobs. Public source coverage is limited and the free Arbeitnow API is not a dedicated Mumbai job feed, so it may return few or no matches. To improve Mumbai coverage, configure Adzuna API credentials or permitted RSS feeds. LinkedIn/Indeed pages are not scraped; use official APIs, permitted feeds, or links you are authorized to access.

The source's `posted_at` date is retained separately from `discovered_at`. The 14-day countdown uses discovery date in this MVP because many feeds do not reliably expose an original posting date. A job can close before the dashboard expiry, and an expired dashboard item is not proof the employer removed its page.

The heuristic match score is a ranking aid, not a probability of getting hired. Salary is never invented. If salary is missing, the app says “Not disclosed”; the compensation component is neutral and the UI warns that fit is unverified.

## Run locally

Requires Python 3.10+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows PowerShell: Copy-Item .env.example .env
# macOS/Linux: cp .env.example .env
streamlit run streamlit_app.py
```

Open the local URL shown by Streamlit. Use the sidebar's **Collect jobs now** button. Or run:

```bash
python job_sources.py
```

The app starts with an empty job feed if no source yields valid matching vacancies; this is intentional.

## Enable AI JD explanations

Add your OpenAI API key to `.env`:

```dotenv
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o-mini
```

Keep `.env` private. Do not commit API keys. AI requests may incur API charges. The rule-based match score works without an AI key.

## Enable additional Mumbai job coverage

### Adzuna
Create an API application in the Adzuna developer portal and set `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env`. Review API terms and quota. Salary fields are shown only when returned by the source.

### RSS
Set `JOB_RSS_FEEDS` to comma-separated RSS feed URLs that you are permitted to use. RSS feeds often provide incomplete company, location, salary and posting-date fields.

### Public API
The MVP checks the public Arbeitnow API, but it is not Mumbai-specific. Results are filtered by geography and role relevance; coverage may be low.

## Daily cloud refresh via GitHub Actions

1. Create a private GitHub repository and upload the project files.
2. In repository Settings → Secrets and variables → Actions, add optional secrets:
   - `ADZUNA_APP_ID`
   - `ADZUNA_APP_KEY`
   - `JOB_RSS_FEEDS`
3. Ensure Actions are enabled. The workflow runs daily at 08:00 India time and can also be run manually under Actions.
4. The workflow updates `data/jobs.json` and commits changes. Do not add API keys to source files.

## Deploy the dashboard to Streamlit Community Cloud

1. Push the project to GitHub.
2. Sign in to Streamlit Community Cloud and create an app from your repository, selecting `streamlit_app.py`.
3. In app settings → Secrets, add:
   ```toml
   OPENAI_API_KEY = "your_key_here"
   OPENAI_MODEL = "gpt-4o-mini"
   ADZUNA_APP_ID = "your_id_here"
   ADZUNA_APP_KEY = "your_key_here"
   ```
   Add only secrets you actually use.
4. Deploy and test the app.
5. Keep daily refresh in GitHub Actions. The workflow commits new job feed data, triggering Streamlit's repository sync/redeploy.

## Persistent cloud application tracking with Supabase (recommended)

The app supports Supabase for persistent application statuses and notes. Without Supabase, it falls back to `data/applications.json`, which is fine locally but is not durable on Streamlit Community Cloud.

1. Create a Supabase project.
2. In the SQL editor, run:

```sql
create table if not exists public.applications (
  job_id text primary key,
  status text not null default 'Interested',
  notes text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.applications enable row level security;
```

3. For this personal MVP, use the project's service-role key only in Streamlit Secrets (never commit it). The service-role key bypasses RLS, so keep the app private and never expose the key to client-side code. A production multi-user app should use authenticated users and per-user RLS policies instead.
4. Add these values in Streamlit app settings → Secrets:

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_KEY = "YOUR_SUPABASE_SERVICE_ROLE_KEY"
```

The application tracker reads/writes to Supabase when configured. Otherwise it uses local JSON. Do not commit private application notes to a public repository.

## 14-day lifecycle

- New jobs get `discovered_at` once.
- Duplicate URLs are merged and keep the original discovery timestamp.
- After 14 days, jobs move out of the active feed and into Archive.
- Archive rows remain stored for reference. This MVP does not delete archived data automatically.

## Files

- `streamlit_app.py` — dashboard UI, filters, match score, JD analysis, application tracker.
- `job_sources.py` — public API, optional Adzuna, RSS collection, deduplication.
- `data/profile.json` — editable candidate profile and filters.
- `data/jobs.json` — collected job records (starts empty).
- `data/applications.json` — local application tracking (starts empty).
- `.github/workflows/daily_refresh.yml` — scheduled collection.
- `.env.example` — configuration template.

## Suggested next iteration

Add Supabase persistence for cloud application tracking, company allow/block lists, alerting, a user-entered CV upload, job-posting freshness validation, and stronger source-specific posting-date normalization.

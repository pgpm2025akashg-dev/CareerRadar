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
- Apify-powered Google Jobs and public LinkedIn job-search collection (optional; requires an Apify token).
- Optional Adzuna API and RSS feed sources.

## Important source and data limitations

This project does not fabricate jobs. The recommended discovery sources are Apify Actors for Google Jobs and public, logged-out LinkedIn job-search results. These are third-party scraping services, so coverage, output fields, pricing and availability can change. Review each Actor’s current documentation, pricing, and applicable platform terms before enabling it. The public Arbeitnow API is not a dedicated Mumbai job feed and may return few or no matches. Adzuna and permitted RSS feeds remain optional alternatives.

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

## Enable Apify Google Jobs and LinkedIn collection (recommended)

1. Create an account at [Apify](https://apify.com/) and copy your API token from **Settings → API & Integrations**.
2. Add the token to your local `.env` file:
   ```dotenv
   APIFY_API_TOKEN=your_apify_token_here
   ```
3. The default Google Jobs Actor is [`googleapi/google-jobs-search-scraper`](https://apify.com/googleapi/google-jobs-search-scraper). The default LinkedIn Actor is [`data_forge_org/linkedin-scraper`](https://apify.com/data_forge_org/linkedin-scraper). You can override the IDs with `APIFY_GOOGLE_ACTOR_ID` and `APIFY_LINKEDIN_ACTOR_ID` if you choose different Actors; check that their input schema matches the configured inputs in `job_sources.py`.
4. Run **Collect jobs now** in the dashboard, or run `python job_sources.py` from the project directory. The collectors search role-specific terms around Mumbai and normalize results into the same feed.
5. For daily refresh on GitHub, add `APIFY_API_TOKEN` under repository **Settings → Secrets and variables → Actions**. Do not put tokens in source code, commit `.env`, or share logs containing secrets.

The Google collector requests listings posted within the last week, while LinkedIn search URLs use a past-week filter; the app independently archives listings 14 days after first discovery. These are different clocks: the archive date is not the job’s original posting date. Actor runs may incur charges depending on the selected Actor and current Apify pricing.

**Privacy and platform note:** the default LinkedIn Actor is configured for public job-search URLs and does not require your LinkedIn password or cookies. Do not provide account credentials or session cookies. Public-page collection can still be subject to platform terms and local rules; review those terms and the Actor’s current documentation before use.

### Adzuna
Create an API application in the Adzuna developer portal and set `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env`. Review API terms and quota. Salary fields are shown only when returned by the source.

### RSS
Set `JOB_RSS_FEEDS` to comma-separated RSS feed URLs that you are permitted to use. RSS feeds often provide incomplete company, location, salary and posting-date fields.

### Public API
The MVP checks the public Arbeitnow API, but it is not Mumbai-specific. Results are filtered by geography and role relevance; coverage may be low.

## Daily cloud refresh via GitHub Actions

1. Create a private GitHub repository and upload the project files.
2. In repository Settings → Secrets and variables → Actions, add optional secrets:
   - `APIFY_API_TOKEN` (recommended)
   - `APIFY_GOOGLE_ACTOR_ID` (optional override)
   - `APIFY_LINKEDIN_ACTOR_ID` (optional override)
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
   APIFY_API_TOKEN = "your_apify_token_here"
   ADZUNA_APP_ID = "your_id_here"
   ADZUNA_APP_KEY = "your_key_here"
   ```
   Add only secrets you actually use.
4. Deploy and test the app.
5. Keep daily refresh in GitHub Actions. The workflow commits new job feed data, triggering Streamlit's repository sync/redeploy.

## Application tracker persistence caveat

The starter app stores application statuses in `data/applications.json`. This is suitable for local use, but Streamlit Community Cloud's local filesystem should not be treated as durable storage. For reliable cloud use, connect a persistent database (e.g. Supabase/Postgres) or use another durable store before relying on application notes across restarts. Do not commit private application notes to a public repository. Use a private repo if retaining the JSON-based feed.

## 14-day lifecycle

- New jobs get `discovered_at` once.
- Duplicate URLs are merged and keep the original discovery timestamp.
- After 14 days, jobs move out of the active feed and into Archive.
- Archive rows remain stored for reference. This MVP does not delete archived data automatically.

## Files

- `streamlit_app.py` — dashboard UI, filters, match score, JD analysis, application tracker.
- `job_sources.py` — Apify Google Jobs and LinkedIn Actors, optional public API/Adzuna/RSS collection, deduplication.
- `data/profile.json` — editable candidate profile and filters.
- `data/jobs.json` — collected job records (starts empty).
- `data/applications.json` — local application tracking (starts empty).
- `.github/workflows/daily_refresh.yml` — scheduled collection.
- `.env.example` — configuration template.

## Suggested next iteration

Add Supabase persistence for cloud application tracking, company allow/block lists, alerting, a user-entered CV upload, job-posting freshness validation, and stronger source-specific posting-date normalization.

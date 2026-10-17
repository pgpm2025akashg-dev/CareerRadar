import os
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import feedparser
from dotenv import load_dotenv

load_dotenv()

def config_value(name, default=""):
    """Read configuration from environment/.env first, then Streamlit Cloud secrets."""
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    try:
        import streamlit as st
        value = st.secrets.get(name, default)
        return str(value) if value not in (None, "") else default
    except Exception:
        # Streamlit secrets are unavailable when running standalone scripts locally.
        return default

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
JOBS_FILE = DATA_DIR / "jobs.json"
TIMEOUT = 20
HEADERS = {"User-Agent": "CareerRadarPersonalJobTracker/1.0 (respectful public API client)"}

APIFY_GOOGLE_ACTOR = config_value("APIFY_GOOGLE_ACTOR_ID", "googleapi/google-jobs-search-scraper")
APIFY_LINKEDIN_ACTOR = config_value("APIFY_LINKEDIN_ACTOR_ID", "data_forge_org/linkedin-scraper")

def first_value(item, *keys):
    """Return the first non-empty value from possible actor output field names."""
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""

def nested_value(item, *path):
    value = item
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if value not in (None, "", [], {}) else ""

def call_apify_actor(actor_id, token, actor_input, logs, source_label):
    """Run an Apify Actor and return dataset rows without ever logging the token/URL."""
    actor_path = actor_id.strip().replace("/", "~")
    endpoint = f"https://api.apify.com/v2/acts/{actor_path}/run-sync-get-dataset-items"
    try:
        response = requests.post(
            endpoint, params={"token": token}, json=actor_input,
            headers={**HEADERS, "Content-Type": "application/json"}, timeout=(10, 240)
        )
        if not response.ok:
            # Do not include request URL or exception text; the URL contains the secret token.
            logs.append(f"{source_label}: Apify returned HTTP {response.status_code}; check Actor ID, token, limits and input settings.")
            return []
        data = response.json()
        if not isinstance(data, list):
            logs.append(f"{source_label}: Apify returned an unexpected response format.")
            return []
        logs.append(f"{source_label}: Apify returned {len(data)} raw result(s).")
        return [x for x in data if isinstance(x, dict)]
    except requests.Timeout:
        logs.append(f"{source_label}: Apify request timed out; try fewer searches or a lower result cap.")
    except requests.RequestException:
        logs.append(f"{source_label}: network/API request failed; verify connectivity and Apify status.")
    except (ValueError, TypeError):
        logs.append(f"{source_label}: Apify response could not be parsed as JSON.")
    return []

def actor_row_to_job(item, source_label):
    """Normalize common Google Jobs / LinkedIn Actor schemas into Career Radar's schema."""
    title = first_value(item, "title", "jobTitle", "position", "name")
    company = first_value(item, "company", "companyName", "company_name", "employer", "organization")
    if isinstance(company, dict):
        company = first_value(company, "name", "displayName", "display_name")
    location = first_value(item, "location", "jobLocation", "formattedLocation", "place")
    if isinstance(location, dict):
        location = first_value(location, "displayName", "name", "address", "formatted")
    description = first_value(item, "description", "jobDescription", "descriptionText", "snippet", "summary")
    url = first_value(item, "url", "jobUrl", "job_url", "link", "applyUrl", "apply_url", "jobPostingUrl")
    if isinstance(url, dict):
        url = first_value(url, "url", "link", "href")
    posted_at = first_value(item, "postedAt", "posted_at", "datePosted", "publishedAt", "published", "date")
    salary = first_value(item, "salary", "salaryRange", "salary_range", "compensation")
    if isinstance(salary, dict):
        salary = first_value(salary, "text", "display", "description", "range")
    tags = first_value(item, "tags", "skills", "extensions")
    if not isinstance(tags, list):
        tags = [str(tags)] if tags else []
    if isinstance(description, dict):
        description = first_value(description, "text", "content", "plainText")
    if not title or not url:
        return None
    return normalize_job({
        "title": title, "company": company, "location": location,
        "description": description, "url": url, "source": source_label,
        "posted_at": posted_at, "salary": salary, "tags": tags
    })

def collect_apify_google(logs):
    token = config_value("APIFY_API_TOKEN", "").strip()
    if not token:
        logs.append("Apify Google Jobs skipped: set APIFY_API_TOKEN to enable.")
        return []
    queries = [
        '"Sourcing Manager" (personal care OR cosmetics OR FMCG)',
        '"Procurement Manager" ("finished goods" OR "contract manufacturing")',
        '"Strategic Sourcing" (beauty OR packaging OR "consumer goods")',
        '"Category Procurement Manager" FMCG',
        '"Vendor Development" procurement',
    ]
    actor_input = {
        "queries": queries, "location": "Mumbai, Maharashtra, India",
        "country": "in", "language": "en", "datePosted": "week",
        "maxItems": 100, "maxPages": 2, "useBrowser": False,
        "includeDescriptionHtml": False
    }
    rows = call_apify_actor(APIFY_GOOGLE_ACTOR, token, actor_input, logs, "Apify Google Jobs")
    jobs = []
    for row in rows:
        job = actor_row_to_job(row, "Apify · Google Jobs")
        if job:
            jobs.append(job)
    logs.append(f"Apify Google Jobs: normalized {len(jobs)} listing(s).")
    return jobs

def collect_apify_linkedin(logs):
    token = config_value("APIFY_API_TOKEN", "").strip()
    if not token:
        logs.append("Apify LinkedIn skipped: set APIFY_API_TOKEN to enable.")
        return []
    from urllib.parse import urlencode
    queries = [
        'Sourcing Manager personal care FMCG',
        'Procurement Manager finished goods',
        'Strategic Sourcing beauty packaging',
        'Category Procurement Manager FMCG',
        'Vendor Development Procurement',
    ]
    search_urls = [
        "https://www.linkedin.com/jobs/search/?" + urlencode({
            "keywords": query, "location": "Mumbai Metropolitan Region", "f_TPR": "r604800"
        })
        for query in queries
    ]
    actor_input = {
        "jobSearchUrls": search_urls, "includeJobDetails": True, "maxJobs": 25
    }
    rows = call_apify_actor(APIFY_LINKEDIN_ACTOR, token, actor_input, logs, "Apify LinkedIn Jobs")
    jobs = []
    for row in rows:
        job = actor_row_to_job(row, "Apify · LinkedIn Jobs")
        if job:
            jobs.append(job)
    logs.append(f"Apify LinkedIn Jobs: normalized {len(jobs)} listing(s).")
    return jobs

def utc_iso():
    return datetime.now(timezone.utc).isoformat()

def clean_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return re.sub(r"\s+", " ", str(value)).strip()

def job_fingerprint(job):
    url = clean_text(job.get("url")).split("?")[0].rstrip("/").lower()
    if url:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    key = "|".join([clean_text(job.get("title")).lower(), clean_text(job.get("company")).lower(), clean_text(job.get("location")).lower()])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

def normalize_job(raw):
    job = dict(raw)
    job["title"] = clean_text(job.get("title"))
    job["company"] = clean_text(job.get("company"))
    job["location"] = clean_text(job.get("location"))
    job["description"] = clean_text(job.get("description"))
    job["salary"] = clean_text(job.get("salary"))
    job["url"] = clean_text(job.get("url"))
    job["source"] = clean_text(job.get("source")) or urlparse(job["url"]).netloc
    job["posted_at"] = clean_text(job.get("posted_at"))
    job["discovered_at"] = clean_text(job.get("discovered_at")) or utc_iso()
    job["tags"] = job.get("tags") if isinstance(job.get("tags"), list) else []
    job["id"] = clean_text(job.get("id")) or job_fingerprint(job)
    return job

def is_mumbai_relevant(job):
    loc = (job.get("location") or "").lower()
    # Remote jobs are retained only if their location explicitly indicates India or the source query is configured for it.
    return "mumbai" in loc or "navi mumbai" in loc or "thane" in loc or "remote" in loc or "india" in loc

def collect_arbeitnow(logs):
    jobs = []
    # Public API. It is not Mumbai-specific; location filter is applied downstream.
    for page in range(1, 4):
        try:
            r = requests.get("https://www.arbeitnow.com/api/job-board-api", params={"page": page}, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            rows = data.get("data", [])
            if not rows:
                break
            for x in rows:
                url = x.get("url") or ""
                title = x.get("title") or ""
                company = x.get("company_name") or ""
                location = x.get("location") or ""
                desc = x.get("description") or ""
                tags = x.get("tags") or []
                # Keep only likely relevant roles and Mumbai/India/remote geography at collection stage.
                hay = " ".join([title, desc, " ".join(tags)]).lower()
                if not any(k in hay for k in ["sourcing", "procurement", "purchasing", "supply chain", "vendor management", "category manager"]):
                    continue
                if not any(k in (location + " " + desc).lower() for k in ["mumbai", "india", "remote", "pune", "thane"]):
                    continue
                jobs.append(normalize_job({
                    "title": title, "company": company, "location": location, "description": desc,
                    "url": url, "source": "Arbeitnow public API", "posted_at": x.get("created_at") or "",
                    "salary": "", "tags": tags
                }))
            if len(rows) < 1:
                break
        except Exception as e:
            logs.append(f"Arbeitnow source unavailable: {e}")
            break
    logs.append(f"Arbeitnow: collected {len(jobs)} potentially relevant listing(s).")
    return jobs

def collect_rss(logs):
    jobs = []
    feeds = [x.strip() for x in config_value("JOB_RSS_FEEDS", "").split(",") if x.strip()]
    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries:
                link = getattr(entry, "link", "")
                title = clean_text(getattr(entry, "title", ""))
                summary = clean_text(getattr(entry, "summary", ""))
                if not link or not title:
                    continue
                published = clean_text(getattr(entry, "published", "") or getattr(entry, "updated", ""))
                jobs.append(normalize_job({
                    "title": title, "company": "", "location": "",
                    "description": summary, "url": link, "source": urlparse(feed_url).netloc + " RSS",
                    "posted_at": published, "salary": "", "tags": []
                }))
        except Exception as e:
            logs.append(f"RSS source failed ({feed_url}): {e}")
    logs.append(f"RSS: collected {len(jobs)} listing(s) from configured feed(s).")
    return jobs

def collect_adzuna(logs):
    app_id = config_value("ADZUNA_APP_ID")
    app_key = config_value("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        logs.append("Adzuna skipped: set ADZUNA_APP_ID and ADZUNA_APP_KEY to enable.")
        return []
    jobs = []
    # Adzuna India API; search terms are deliberately role-specific.
    queries = ["sourcing manager", "procurement manager", "supply chain manager", "category sourcing"]
    for query in queries:
        try:
            url = f"https://api.adzuna.com/v1/api/jobs/in/search/1"
            params = {
                "app_id": app_id, "app_key": app_key, "results_per_page": 50,
                "what": query, "where": "Mumbai", "content-type": "application/json"
            }
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            for x in r.json().get("results", []):
                company = (x.get("company") or {}).get("display_name", "")
                location = (x.get("location") or {}).get("display_name", "")
                salary_min, salary_max = x.get("salary_min"), x.get("salary_max")
                salary = ""
                if salary_min and salary_max:
                    salary = f"₹{salary_min:,.0f}–₹{salary_max:,.0f} per year (source-reported)"
                jobs.append(normalize_job({
                    "title": x.get("title", ""), "company": company, "location": location,
                    "description": x.get("description", ""), "url": x.get("redirect_url", ""),
                    "source": "Adzuna API", "posted_at": x.get("created", ""),
                    "salary": salary, "tags": [query]
                }))
        except Exception as e:
            logs.append(f"Adzuna query '{query}' failed: {e}")
    logs.append(f"Adzuna: collected {len(jobs)} listing(s).")
    return jobs

def collect_jobs():
    logs = []
    jobs = []
    # Optional Apify sources materially improve Mumbai-specific Google/LinkedIn coverage.
    # If APIFY_API_TOKEN is absent, these collectors safely skip.
    jobs.extend(collect_apify_google(logs))
    jobs.extend(collect_apify_linkedin(logs))
    jobs.extend(collect_arbeitnow(logs))
    jobs.extend(collect_rss(logs))
    jobs.extend(collect_adzuna(logs))

    # Restrict broad results to target geography when the location is known.
    filtered = []
    for j in jobs:
        if is_mumbai_relevant(j):
            filtered.append(j)
    # De-duplicate the fresh batch by canonical URL fingerprint.
    unique = {}
    for j in filtered:
        unique.setdefault(job_fingerprint(j), j)
    fresh = list(unique.values())

    # Keep the original discovered_at for known jobs; set it only for new jobs.
    existing = []
    if JOBS_FILE.exists():
        try:
            existing = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    old_by_key = {job_fingerprint(x): x for x in existing if x.get("url")}
    for j in fresh:
        key = job_fingerprint(j)
        if key in old_by_key:
            j["discovered_at"] = old_by_key[key].get("discovered_at") or utc_iso()
            j["id"] = old_by_key[key].get("id") or key
        else:
            j["discovered_at"] = utc_iso()
    return fresh, logs

if __name__ == "__main__":
    jobs, logs = collect_jobs()
    existing = []
    if JOBS_FILE.exists():
        try:
            existing = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    by_key = {job_fingerprint(x): normalize_job(x) for x in existing if x.get("url")}
    added = 0
    for job in jobs:
        key = job_fingerprint(job)
        if key not in by_key:
            by_key[key] = normalize_job(job)
            added += 1
        else:
            old = by_key[key]
            # Refresh source metadata without resetting first discovery date.
            for k in ("description", "salary", "location", "company", "title", "posted_at", "source", "tags"):
                if job.get(k):
                    old[k] = job[k]
    JOBS_FILE.parent.mkdir(exist_ok=True)
    JOBS_FILE.write_text(json.dumps(list(by_key.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(logs))
    print(f"New unique jobs added: {added}; total stored: {len(by_key)}")

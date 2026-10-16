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
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
JOBS_FILE = DATA_DIR / "jobs.json"
TIMEOUT = 20
HEADERS = {"User-Agent": "CareerRadarPersonalJobTracker/1.0 (respectful public API client)"}

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
    feeds = [x.strip() for x in os.getenv("JOB_RSS_FEEDS", "").split(",") if x.strip()]
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
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
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
    # Public API first; optional sources can improve India/Mumbai coverage.
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

import os
import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from job_sources import collect_jobs, normalize_job, job_fingerprint

load_dotenv()
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
JOBS_FILE = DATA_DIR / "jobs.json"
APPLICATIONS_FILE = DATA_DIR / "applications.json"
PROFILE_FILE = DATA_DIR / "profile.json"

st.set_page_config(page_title="Career Radar", page_icon="🎯", layout="wide")

DEFAULT_PROFILE = {
    "current_role": "Finished Goods Sourcing Manager",
    "current_company": "Purplle",
    "current_ctc_lpa": 18,
    "target_min_lpa": 22,
    "target_max_lpa": 25,
    "location": "Mumbai",
    "experience_summary": (
        "Finished Goods Sourcing Manager at Purplle; approximately ₹100 Cr annual sourcing spend; "
        "200+ SKUs; 21 new product launches; 30% packaging cost reduction; reduced ₹3 Cr inventory "
        "exposure through manufacturer-owned inventory; prior industrial operations, procurement, "
        "cost reduction, SAP and digital transformation experience; SPJIMR PGPM."
    ),
    "skills": [
        "strategic sourcing", "procurement", "finished goods", "contract manufacturing",
        "vendor management", "supplier development", "cost reduction", "negotiation",
        "packaging", "personal care", "beauty", "FMCG", "new product development",
        "NPD", "production planning", "inventory optimization", "forecasting", "SAP",
        "operations", "process improvement", "supply chain"
    ],
    "exclude_terms": [
        "sales", "business development", "investment banking", "financial analyst",
        "finance manager", "account executive", "wealth management", "insurance sales"
    ],
    "include_terms": [
        "sourcing", "procurement", "purchasing", "category management", "supply chain",
        "contract manufacturing", "vendor management", "strategic sourcing",
        "materials management", "supply planning"
    ]
}

def utc_now():
    return datetime.now(timezone.utc)

def parse_dt(value):
    if not value:
        return None
    try:
        v = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def load_profile():
    p = load_json(PROFILE_FILE, DEFAULT_PROFILE)
    # Preserve default keys if a profile file is partially configured.
    return {**DEFAULT_PROFILE, **p}

def load_jobs():
    jobs = load_json(JOBS_FILE, [])
    out = []
    for raw in jobs:
        try:
            j = normalize_job(raw)
            if j.get("url") and j.get("title"):
                out.append(j)
        except Exception:
            continue
    return out

def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None

def load_applications():
    client = get_supabase()
    if client:
        try:
            rows = client.table("applications").select("*").execute().data or []
            return {row["job_id"]: {
                "status": row.get("status", "Interested"),
                "notes": row.get("notes", ""),
                "updated_at": row.get("updated_at", ""),
                "created_at": row.get("created_at", "")
            } for row in rows}
        except Exception as exc:
            st.warning(f"Supabase read failed; using local application tracker: {exc}")
    return load_json(APPLICATIONS_FILE, {})

def save_application(job_id, status, notes=""):
    apps = load_applications()
    prior = apps.get(job_id, {})
    record = {
        "job_id": job_id,
        "status": status,
        "notes": notes,
        "updated_at": utc_now().isoformat(),
        "created_at": prior.get("created_at", utc_now().isoformat())
    }
    client = get_supabase()
    if client:
        try:
            client.table("applications").upsert(record, on_conflict="job_id").execute()
            return
        except Exception as exc:
            st.warning(f"Supabase write failed; saving locally instead: {exc}")
    apps[job_id] = {k: v for k, v in record.items() if k != "job_id"}
    save_json(APPLICATIONS_FILE, apps)

def score_job(job, profile):
    title = (job.get("title") or "").lower()
    company = (job.get("company") or "").lower()
    text = " ".join([
        title, company, job.get("description") or "",
        job.get("location") or "", " ".join(job.get("tags") or [])
    ]).lower()

    # Exclude finance/sales and unrelated roles before scoring.
    exclude = [x.lower() for x in profile.get("exclude_terms", [])]
    if any(term in title for term in exclude):
        return {"score": 0, "excluded": True, "reason": "Excluded job family (finance/sales or similar)", "factors": {}}

    includes = [x.lower() for x in profile.get("include_terms", [])]
    if not any(term in title or term in text for term in includes):
        return {"score": 0, "excluded": True, "reason": "No relevant sourcing/procurement/supply-chain signal", "factors": {}}

    skills = [x.lower() for x in profile.get("skills", [])]
    matched_skills = [x for x in skills if x in text]
    sourcing_terms = ["sourcing", "procurement", "supplier", "vendor", "purchasing", "category management"]
    industry_terms = ["personal care", "beauty", "cosmetic", "fmcg", "consumer", "fragrance", "packaging", "contract manufacturing"]
    seniority_terms = ["manager", "lead", "senior", "head", "category"]
    tools_terms = ["sap", "forecast", "inventory", "cost reduction", "negotiation", "new product", "npd", "planning"]

    def coverage(terms):
        return min(1.0, sum(1 for term in terms if term in text) / max(1, min(4, len(terms))))

    sourcing = 30 * coverage(sourcing_terms)
    industry = 20 * coverage(industry_terms)
    seniority = 15 * coverage(seniority_terms)
    tools = 15 * coverage(tools_terms)

    salary = 7.5  # Neutral midpoint when salary is unknown; not a salary estimate.
    salary_text = str(job.get("salary") or "").lower()
    if salary_text:
        import re
        nums = [float(n) for n in re.findall(r'\d+(?:\.\d+)?', salary_text.replace(",", ""))]
        if nums:
            # Use only explicit salary text; simple heuristic, clearly not verified by model.
            if any(n >= 22 for n in nums):
                salary = 15
            elif any(n >= 18 for n in nums):
                salary = 10
            else:
                salary = 2
    location_text = (job.get("location") or "").lower()
    location = 5 if ("mumbai" in location_text or "remote" in location_text or "hybrid" in location_text) else 1
    total = round(sourcing + industry + seniority + tools + salary + location)
    why = []
    if matched_skills:
        why.append("Matched skills: " + ", ".join(matched_skills[:8]))
    if "personal care" in text or "beauty" in text or "cosmetic" in text:
        why.append("Relevant consumer/beauty category signal")
    if "supplier" in text or "vendor" in text or "procurement" in text:
        why.append("Supplier or procurement responsibilities detected")
    if not job.get("salary"):
        why.append("Salary not disclosed; compensation fit is unverified")
    return {
        "score": min(100, total), "excluded": False,
        "reason": "; ".join(why) if why else "Relevant role keywords found; review the JD for detail.",
        "factors": {
            "Sourcing / procurement (30)": round(sourcing),
            "Industry relevance (20)": round(industry),
            "Seniority (15)": round(seniority),
            "Skills / tools (15)": round(tools),
            "Compensation (15)": round(salary),
            "Location (5)": round(location)
        }
    }

def is_expired(job, days=14):
    discovered = parse_dt(job.get("discovered_at"))
    return bool(discovered and utc_now() >= discovered + timedelta(days=days))

def source_label(job):
    return job.get("source") or urlparse(job.get("url", "")).netloc

def ai_explain(job, profile):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, "Add OPENAI_API_KEY to your environment to enable AI explanations. The rule-based match score still works."
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        prompt = f"""
You are a practical career coach. Analyze the vacancy against this candidate profile.
Candidate: {profile.get('experience_summary')}
Target CTC: INR {profile.get('target_min_lpa')}–{profile.get('target_max_lpa')} LPA.
Preferred location: {profile.get('location')}.
Job title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Salary as provided by source: {job.get('salary') or 'Not disclosed'}
Job description:
{(job.get('description') or 'No full description available from source')[:12000]}

Return concise Markdown with these headings:
1. **What this job means in plain English**
2. **Likely day-to-day responsibilities**
3. **Where the candidate matches** (use specific evidence from profile)
4. **Gaps / questions to validate** (do not invent candidate experience)
5. **Compensation assessment** (never guess salary; state if undisclosed)
6. **Recommendation** (Strong fit / Possible fit / Weak fit and why)
Be candid and do not infer facts not supplied.
"""
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You explain job descriptions and assess career fit. Never fabricate vacancy or salary facts."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        return resp.choices[0].message.content, None
    except Exception as e:
        return None, f"AI request failed: {e}"

# Header
st.title("🎯 Career Radar")
st.caption("Personal job discovery and sourcing-career fit dashboard · Mumbai")
profile = load_profile()
jobs = load_jobs()
apps = load_applications()

# Expired jobs are archived from the active feed, not deleted.
active_jobs = [j for j in jobs if not is_expired(j)]
expired_jobs = [j for j in jobs if is_expired(j)]
if len(active_jobs) != len(jobs):
    st.caption(f"{len(expired_jobs)} listing(s) are past the 14-day active window and shown only in Archive.")

# Compute scores and exclusion flags
scored = []
for job in active_jobs:
    s = score_job(job, profile)
    scored.append((job, s))
eligible = [(j, s) for j, s in scored if not s["excluded"]]
today = utc_now().date()
new_today = sum(1 for j, _ in eligible if parse_dt(j.get("discovered_at")) and parse_dt(j.get("discovered_at")).date() == today)
strong = sum(1 for _, s in eligible if s["score"] >= 70)
applied = sum(1 for x in apps.values() if x.get("status") in ("Applied", "Interviewing", "Offer"))

m1, m2, m3, m4 = st.columns(4)
m1.metric("Active relevant jobs", len(eligible))
m2.metric("Discovered today", new_today)
m3.metric("Strong matches (70+)", strong)
m4.metric("Applications in progress", applied)

with st.sidebar:
    st.header("Filters")
    locations = st.multiselect("Location", ["Mumbai", "Pune", "Remote", "India-wide", "Other"], default=["Mumbai"])
    min_score = st.slider("Minimum match score", 0, 100, 35, 5)
    age = st.selectbox("Discovered within", ["Any time", "24 hours", "3 days", "7 days", "14 days"], index=4)
    salary_filter = st.selectbox("Salary disclosure", ["Any", "Disclosed only", "Not disclosed"], index=0)
    statuses = ["All", "Interested", "To Apply", "Applied", "Interviewing", "Rejected", "Offer"]
    app_filter = st.selectbox("Application status", statuses, index=0)
    include_expired = st.checkbox("Show expired jobs in Archive", value=False)
    st.divider()
    st.subheader("Refresh")
    if st.button("Collect jobs now", use_container_width=True):
        with st.spinner("Collecting from configured sources..."):
            fresh, logs = collect_jobs()
            existing = load_json(JOBS_FILE, [])
            by_key = {job_fingerprint(x): normalize_job(x) for x in existing if x.get("url")}
            added = 0
            for j in fresh:
                key = job_fingerprint(j)
                if key not in by_key:
                    by_key[key] = j
                    added += 1
                else:
                    # Keep first discovery timestamp; refresh description and source metadata.
                    old = by_key[key]
                    for k in ("description", "salary", "location", "company", "title", "posted_at"):
                        if j.get(k) and not old.get(k):
                            old[k] = j[k]
            save_json(JOBS_FILE, list(by_key.values()))
        st.success(f"Added {added} new listing(s).")
        for log in logs:
            st.caption(log)
        st.rerun()
    st.caption("Daily refresh is configured through GitHub Actions after deployment.")

tabs = st.tabs(["🔎 Job feed", "📌 Application tracker", "🗄️ Archive", "⚙️ Profile & setup"])

with tabs[0]:
    st.subheader("Relevant openings")
    st.caption("Only listings collected from configured sources are shown. Match scores are heuristic, not hiring predictions.")
    filtered = []
    now = utc_now()
    for job, score in eligible:
        loc = (job.get("location") or "").lower()
        if locations:
            location_ok = (
                ("Mumbai" in locations and "mumbai" in loc)
                or ("Pune" in locations and "pune" in loc)
                or ("Remote" in locations and ("remote" in loc or "anywhere" in loc))
                or ("India-wide" in locations and ("india" in loc or "pan india" in loc))
                or ("Other" in locations and not any(x in loc for x in ["mumbai", "pune", "remote", "india"]))
            )
            if not location_ok:
                continue
        if score["score"] < min_score:
            continue
        discovered = parse_dt(job.get("discovered_at"))
        if age != "Any time" and discovered:
            days = {"24 hours": 1, "3 days": 3, "7 days": 7, "14 days": 14}[age]
            if now - discovered > timedelta(days=days):
                continue
        if salary_filter == "Disclosed only" and not job.get("salary"):
            continue
        if salary_filter == "Not disclosed" and job.get("salary"):
            continue
        app_status = apps.get(job.get("id"), {}).get("status", "Interested")
        if app_filter != "All" and app_status != app_filter:
            continue
        filtered.append((job, score))

    filtered.sort(key=lambda x: (x[1]["score"], x[0].get("discovered_at", "")), reverse=True)
    if not filtered:
        st.info("No matching jobs are in the database yet. Add source configuration and use “Collect jobs now”, or run the scheduled collector. The app intentionally does not fabricate listings.")
    for job, score in filtered:
        job_id = job.get("id") or job_fingerprint(job)
        discovered = parse_dt(job.get("discovered_at"))
        age_days = max(0, (utc_now() - discovered).days) if discovered else None
        remaining = max(0, 14 - age_days) if age_days is not None else None
        with st.container(border=True):
            left, right = st.columns([5, 1])
            with left:
                st.markdown(f"### [{job.get('title','Untitled role')}]({job.get('url','#')})")
                st.write(f"**{job.get('company') or 'Company not provided'}** · {job.get('location') or 'Location not provided'}")
                st.caption(f"Source: {source_label(job)} · Posted: {job.get('posted_at') or 'Not provided'} · Discovered: {discovered.strftime('%d %b %Y') if discovered else 'Unknown'} · {remaining if remaining is not None else '—'} days remaining")
                st.write(f"**Salary:** {job.get('salary') or 'Not disclosed'}")
                st.write(score["reason"])
            with right:
                st.metric("Match", f"{score['score']}%")
                st.progress(score["score"] / 100)
            c1, c2, c3 = st.columns([1, 1, 2])
            with c1:
                if st.button("Explain JD", key=f"explain_{job_id}"):
                    st.session_state["explain_job_id"] = job_id
            with c2:
                current_status = apps.get(job_id, {}).get("status", "Interested")
                new_status = st.selectbox("Status", ["Interested", "To Apply", "Applied", "Interviewing", "Rejected", "Offer"], index=["Interested", "To Apply", "Applied", "Interviewing", "Rejected", "Offer"].index(current_status), key=f"status_{job_id}", label_visibility="collapsed")
                if new_status != current_status:
                    save_application(job_id, new_status, apps.get(job_id, {}).get("notes", ""))
                    st.rerun()
            with c3:
                with st.expander("See match breakdown"):
                    for factor, points in score["factors"].items():
                        st.write(f"- {factor}: **{points}**")
                    st.caption("Weights: sourcing 30, industry 20, seniority 15, skills/tools 15, compensation 15, location 5.")
                    st.caption("Unknown salary is scored neutrally; this does not mean the job pays within your target.")
            if st.session_state.get("explain_job_id") == job_id:
                with st.expander("AI job analysis", expanded=True):
                    with st.spinner("Explaining the JD and comparing it with your profile..."):
                        explanation, error = ai_explain(job, profile)
                    if explanation:
                        st.markdown(explanation)
                    else:
                        st.warning(error)
                    if st.button("Close analysis", key=f"close_{job_id}"):
                        st.session_state.pop("explain_job_id", None)
                        st.rerun()

with tabs[1]:
    st.subheader("Application tracker")
    if not apps:
        st.info("Set a job to Interested, To Apply, Applied, Interviewing, Rejected or Offer from the job feed to track it here.")
    else:
        app_rows = []
        jobs_by_id = {j.get("id") or job_fingerprint(j): j for j in jobs}
        for jid, entry in apps.items():
            job = jobs_by_id.get(jid, {})
            app_rows.append({
                "Job title": job.get("title", jid),
                "Company": job.get("company", ""),
                "Status": entry.get("status", ""),
                "Updated": entry.get("updated_at", ""),
                "URL": job.get("url", ""),
                "Notes": entry.get("notes", "")
            })
        st.dataframe(pd.DataFrame(app_rows), use_container_width=True, hide_index=True)
        st.download_button("Export tracker CSV", pd.DataFrame(app_rows).to_csv(index=False).encode("utf-8"), "career_radar_applications.csv", "text/csv")

with tabs[2]:
    st.subheader("Archive · expired after 14 days")
    if not expired_jobs:
        st.info("No expired listings in the archive.")
    else:
        archive_rows = []
        for j in expired_jobs:
            s = score_job(j, profile)
            archive_rows.append({
                "Title": j.get("title"), "Company": j.get("company"), "Location": j.get("location"),
                "Discovered": j.get("discovered_at"), "Score": s["score"], "URL": j.get("url")
            })
        st.dataframe(pd.DataFrame(archive_rows), use_container_width=True, hide_index=True)
        st.caption("Archived listings remain available for reference. The original employer listing may expire earlier or later.")

with tabs[3]:
    st.subheader("Your profile")
    st.write(f"**Current role:** {profile['current_role']} at {profile['current_company']}")
    st.write(f"**Current CTC:** ₹{profile['current_ctc_lpa']} LPA · **Target:** ₹{profile['target_min_lpa']}–{profile['target_max_lpa']} LPA")
    st.write(f"**Location:** {profile['location']}")
    st.write(profile["experience_summary"])
    st.write("**Excluded by default:** " + ", ".join(profile["exclude_terms"]))
    st.write("**Included job families:** " + ", ".join(profile["include_terms"]))
    st.caption("Edit data/profile.json to customize your profile, target salary, inclusion terms and exclusion terms.")
    st.subheader("Data source status")
    st.write("Sources are configured in `.env` and `job_sources.py`. Free public APIs may have geographic coverage limits; source availability and posting dates are not guaranteed.")
    st.write("Daily refresh is performed by `.github/workflows/daily_refresh.yml` after GitHub deployment.")
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"):
        st.success("Supabase configured: application status and notes use persistent cloud storage.")
    else:
        st.warning("Supabase not configured: application tracker uses local JSON, which may reset on cloud restarts. Configure Supabase for persistent cloud tracking.")

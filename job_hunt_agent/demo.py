"""`job-hunt-agent demo`: the whole app on fictional data, with no keys and no network.

A fresh app home (a temp folder unless --home is given) gets the two sample profiles, about 30 made-up postings from
made-up companies on the real ATS shapes (Greenhouse, Lever, Ashby, Workday), a tracker with a few weeks of
applications, statuses, contacts and follow-ups, and two tailored resumes. JOB_HUNT_AGENT_DEMO=1 switches every
outside service to a fake: no fetch, no Google, no Claude (the tailor and outreach use their no-Claude paths),
Setup's Test buttons answer "demo mode".
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

COMPANIES = [  # (name, ats, board key, domain, city)
    ("Northstar Robotics", "greenhouse", "northstarrobotics", "northstar-robotics.example", "Austin, TX"),
    ("Lumen Health AI", "lever", "lumenhealth", "lumenhealth.example", "Boston, MA"),
    ("Cobalt Data", "ashby", "cobaltdata", "cobaltdata.example", "Remote, US"),
    ("Tidewater Logistics", "workday", "tidewater", "tidewater.example", "Seattle, WA"),
    ("Juniper Grid", "greenhouse", "junipergrid", "junipergrid.example", "Denver, CO"),
    ("Vantage Point Labs", "ashby", "vantagepoint", "vantagepoint.example", "New York, NY"),
    ("Helix Bio", "lever", "helixbio", "helixbio.example", "San Diego, CA"),
    ("Orbital Systems", "greenhouse", "orbitalsystems", "orbital.example", "Remote, US"),
]
ROLES = [  # (title, family, required skills, nice to have, years, salary)
    ("Machine Learning Engineer", "ml_engineer", "Python, PyTorch, AWS, Docker, Kubernetes and MLflow",
     "Spark, Airflow", 3, (150, 190)),
    ("Senior AI Engineer, LLM Applications", "ai_engineer", "Python, LLM, RAG, LangChain, FastAPI and AWS",
     "Kubernetes, vector databases", 5, (175, 220)),
    ("Applied Scientist, NLP", "ai_engineer", "Python, PyTorch, NLP, LLM and experiment design", "JAX", 4,
     (165, 210)),
    ("Data Scientist, Product", "data_scientist", "Python, SQL, A/B testing, scikit-learn and Tableau",
     "XGBoost, causal inference", 2, (130, 165)),
    ("Data Engineer", "data_engineer", "Python, SQL, Spark, Airflow, dbt and Snowflake", "Kafka", 3, (135, 170)),
    ("ML Platform Engineer", "ml_engineer", "Python, Kubernetes, Docker, MLflow and Terraform", "Ray", 4,
     (160, 200)),
    ("Generative AI Engineer", "ai_engineer", "Python, LLM, RAG, LangChain and evaluation of model outputs",
     "fine-tuning", 2, (140, 185)),
    ("Computer Vision Engineer", "ml_engineer", "Python, PyTorch, OpenCV and CUDA", "TensorRT", 4, (155, 195)),
]
JD = """About the team
The {team} team at {company} builds the models behind our product. You will work with engineers, scientists and
product managers to ship features that customers use every day.

What you will do
- Design, train and ship {what} to production
- Own evaluation: offline metrics, online experiments and regression checks
- Work with data engineering on reliable pipelines and feature stores
- Write clear docs and review code with the team

Requirements
- {years}+ years of experience in a similar role
- Strong skills in {required}
- Experience taking models from a notebook to a monitored production service

Nice to have
- {nice}

Compensation
The base salary range for this role is ${lo},000 to ${hi},000 per year, plus equity and benefits.
"""
TEAMS = ("Applied ML", "Search and Ranking", "Platform", "Forecasting", "Assistant", "Perception")


def _job(n: int, company: tuple, role: tuple, hours_ago: float, now: datetime) -> dict:
    from .ats.common import make_job
    name, ats, key, domain, city = company
    title, _, required, nice, years, (lo, hi) = role
    posted = (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    what = "LLM features" if "LLM" in required or "NLP" in required else "models and data products"
    body = JD.format(team=TEAMS[n % len(TEAMS)], company=name, what=what, years=years, required=required,
                     nice=nice, lo=lo, hi=hi)
    url = {"greenhouse": f"https://job-boards.greenhouse.io/{key}/jobs/{9000 + n}",
           "lever": f"https://jobs.lever.co/{key}/demo-{n}",
           "ashby": f"https://jobs.ashbyhq.com/{key}/demo-{n}",
           "workday": f"https://{key}.wd5.myworkdayjobs.com/en-US/careers/job/demo-{n}"}[ats]
    return make_job(ats=ats, key=key, ext_id=str(9000 + n), company=name, title=title, location=city,
                    workplace="remote" if city.startswith("Remote") else "hybrid", url=url, posted_at=posted,
                    description=body, req_id=f"R-{2400 + n}")


def seed(home: Path, now: datetime | None = None) -> dict:
    """Fill an empty app home with the demo data. Returns counts."""
    from . import api, config, contacts, db, fits, sample, store, tailor, tracker
    config.set_home(home)
    from .ats.common import FetchResult
    now = now or db.utcnow()
    config.PROFILES.mkdir(parents=True, exist_ok=True)
    sample.write(config.PROFILES)
    conn = db.connect()
    boards = [{"board": f"{c[1]}:{c[2]}", "ats": c[1], "key": c[2], "company": c[0]} for c in COMPANIES]
    db.sync_boards(conn, boards)
    run = db.start_run(conn, "demo", now)
    ages = [0.3, 0.6, 1.2, 2, 3.5, 5, 7, 9, 12, 16, 20, 23, 28, 34, 40, 47, 55, 62, 70, 80, 96, 110, 130, 150]
    jobs_by_board: dict[str, list] = {}
    for n, hours in enumerate(ages):
        company = COMPANIES[n % len(COMPANIES)]
        role = ROLES[(n * 3 + n // len(ROLES)) % len(ROLES)]
        jobs_by_board.setdefault(f"{company[1]}:{company[2]}", []).append(_job(n, company, role, hours, now))
    for board, items in jobs_by_board.items():
        store.write_board(conn, db.board(conn, board), FetchResult(items, 200, n_raw=len(items)), "ok", run, now,
                          [f"fam:{r[1]}" for r in ROLES], 5)
    for pid in config.profile_ids():
        fits.refresh(conn, config.profile(pid), now)
    uids = [j["uid"] for items in jobs_by_board.values() for j in items]
    old = sorted(uids, key=lambda u: -int(u.rsplit(":", 1)[1]))      # older postings were applied to earlier
    plan = [(0, "interview"), (1, "screen"), (2, "applied"), (3, "rejected"), (5, "applied"), (6, "applied"),
            (8, "applied"), (9, "ghosted"), (11, "applied")]
    applied = []
    for i, (days_ago, status) in enumerate(plan):
        uid = old[i]
        app_id = api.mark_applied(conn, "alex", uid, now)
        day = (now - timedelta(days=days_ago)).date()
        tracker.edit_field(conn, app_id, "date_applied", day.strftime("%m/%d/%Y"))
        with db.tx(conn):
            conn.execute("UPDATE applications SET applied_on = ? WHERE id = ?", (day.isoformat(), app_id))
        if status != "applied":
            tracker.set_status(conn, app_id, status, source="ui")
        applied.append(app_id)
    people = [("Priya Shah", "Engineering Manager, Applied ML"), ("Sam Okafor", "Technical Recruiter"),
              ("Dana Wu", "Senior ML Engineer")]
    for app_id, (name, role) in zip(applied[:3], people):
        company = conn.execute("SELECT company FROM applications WHERE id = ?", (app_id,)).fetchone()["company"]
        domain = next(c[3] for c in COMPANIES if c[0] == company)
        first = name.split()[0].lower()
        contacts.save(conn, app_id, [{"name": name, "role": role, "email": f"{first}@{domain}",
                                      "linkedin": "", "notes": "", "source": "manual"}])
    jordan_uid = next(u for u in uids if u not in old[:len(plan)])
    api.mark_applied(conn, "jordan", jordan_uid, now)
    conn.close()
    fresh = [u for u in uids if u not in old[:len(plan)]][:2]
    for uid in fresh:                                             # two tailored resumes, made the no-Claude way
        conn = db.connect()
        row = conn.execute("SELECT company, title, description FROM jobs WHERE uid = ?", (uid,)).fetchone()
        conn.close()
        with tailor._LOCK:                       # the worker _queue starts waits, then finds the job already done
            job_id = tailor._queue({"profile": "alex", "company": row["company"], "role": row["title"],
                                    "jd_text": db.unpack(row["description"]), "source_job_id": uid})
            job = tailor._load(job_id)
            tailor._save(tailor._finish(job, tailor.demo_tailor(tailor.base_resume("alex"), job)))
    return {"jobs": len(uids), "applications": len(plan) + 1, "home": str(home)}


def run(home: str | None = None, port: int | None = None, open_browser: bool = False) -> int:
    """Seed a demo home and serve it (fetch, sync and mail off)."""
    target = Path(home).expanduser() if home else Path(tempfile.mkdtemp(prefix="job-hunt-agent-demo-"))
    if (target / "data").exists() and any((target / "data").iterdir()):
        print(f"{target} already has data: using it as it is")
        from . import config
        config.set_home(target)
    else:
        print(f"Seeding demo data in {target} ...", flush=True)
        seed(target, datetime.now(timezone.utc))
    os.environ["JOB_HUNT_AGENT_DEMO"] = "1"
    from . import server
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.5, webbrowser.open, (f"http://localhost:{port or 8765}/",)).start()
    print("Demo mode: fictional data, no keys, nothing sent anywhere. Ctrl-C to stop.", flush=True)
    server.serve(port=port, fetch_loop=False, sync=False)
    return 0
